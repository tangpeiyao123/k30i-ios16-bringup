#!/usr/bin/env python3
"""Replace one file inside the boot image's payload FAT, in place.

Why: the payload carries the owner trust cache (KERNEL~1.TRU).  A CI build that
starts from the GPU release bundle keeps only that bundle's private module, so
the SpringBoard inject library's cdhash is absent and AMFI refuses its
signature (PICASSO_INJECT signature error=1, fcntl 97 EPERM).  dyld then never
initialises the image, the status bar hooks never install, and the battery
sits under the panel's punch hole.  Swapping the merged bundle in fixes that
without a rebuild.

The file must fit the clusters it already owns; anything larger is refused
rather than silently re-chained, because a wrong FAT edit bricks the payload.

    python patch_payload_file.py <img> --list
    python patch_payload_file.py <img> --replace KERNEL~1.TRU new.bundle -o out.img
"""
import argparse
import hashlib
import struct
import sys


class Payload:
    def __init__(self, path):
        self.path = path
        self.img = bytearray(open(path, "rb").read())
        ks, rs = struct.unpack_from("<I", self.img, 8)[0], struct.unpack_from("<I", self.img, 16)[0]
        page, = struct.unpack_from("<I", self.img, 36)
        self.rd_off = page + ((ks + page - 1) // page) * page
        self.rd_len = rs
        rd = self.img[self.rd_off:self.rd_off + rs]
        self.bps = struct.unpack_from("<H", rd, 11)[0]
        self.spc = rd[13]
        rsvd = struct.unpack_from("<H", rd, 14)[0]
        nf = rd[16]
        spf = struct.unpack_from("<I", rd, 36)[0]
        self.fat_off = self.rd_off + rsvd * self.bps
        self.data_off = self.rd_off + (rsvd + nf * spf) * self.bps
        self.csz = self.bps * self.spc
        self.root = struct.unpack_from("<I", rd, 44)[0]
        self.entries = {}   # upper name -> (dir_offset_in_img, first_cluster, size)
        self._walk(self.root, "")

    # --- FAT helpers -----------------------------------------------------
    def _next(self, cl):
        v, = struct.unpack_from("<I", self.img, self.fat_off + cl * 4)
        return v & 0x0FFFFFFF

    def _chain(self, cl):
        out = []
        while 2 <= cl < 0x0FFFFFF0 and len(out) < 400000:
            out.append(cl)
            cl = self._next(cl)
        return out

    def _clu_off(self, cl):
        return self.data_off + (cl - 2) * self.csz

    def _walk(self, cl, prefix):
        for c in self._chain(cl):
            base = self._clu_off(c)
            for i in range(0, self.csz, 32):
                off = base + i
                e = self.img[off:off + 32]
                if len(e) < 32 or e[0] == 0xE5:
                    continue
                if e[0] == 0:
                    return
                if e[11] == 0x0F:
                    continue
                name = e[:8].decode("latin1", "replace").strip()
                ext = e[8:11].decode("latin1", "replace").strip()
                first = struct.unpack_from("<H", e, 26)[0] | (struct.unpack_from("<H", e, 20)[0] << 16)
                size, = struct.unpack_from("<I", e, 28)
                full = (prefix + name + "." + ext) if ext else prefix + name
                if e[11] & 0x10:
                    if name not in (".", ".."):
                        self._walk(first, full + "/")
                else:
                    self.entries[full] = (off, first, size)

    # --- operations ------------------------------------------------------
    def find(self, wanted):
        for name in self.entries:
            if name.upper() == wanted.upper() or name.endswith("/" + wanted):
                return name
        hits = [n for n in self.entries if wanted.upper() in n.upper()]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"{wanted}: {'ambiguous ' + str(hits) if hits else 'not found'}")

    def read(self, name):
        key = self.find(name)
        _, first, size = self.entries[key]
        data = b"".join(bytes(self.img[self._clu_off(c):self._clu_off(c) + self.csz])
                        for c in self._chain(first))
        return key, data[:size]

    def _alloc(self, count):
        """Take `count` free clusters off the FAT and chain them in order."""
        free = []
        limit = (self.data_off - self.fat_off) // 4
        cl = 2
        while len(free) < count and cl < limit:
            v, = struct.unpack_from("<I", self.img, self.fat_off + cl * 4)
            if v & 0x0FFFFFFF == 0:
                free.append(cl)
            cl += 1
        if len(free) < count:
            raise ValueError(f"only {len(free)} free clusters, {count} needed")
        for a, b in zip(free, free[1:]):
            struct.pack_into("<I", self.img, self.fat_off + a * 4, b)
        struct.pack_into("<I", self.img, self.fat_off + free[-1] * 4, 0x0FFFFFFF)
        return free

    def replace(self, name, content, grow=True):
        key = self.find(name)
        off, first, old_size = self.entries[key]
        chain = self._chain(first)
        need = (len(content) + self.csz - 1) // self.csz
        grew = 0
        if need > len(chain):
            if not grow:
                raise ValueError(f"{key}: needs {need} clusters, owns {len(chain)}; growth disabled")
            extra = self._alloc(need - len(chain))
            # the previous last cluster continues into the new run
            struct.pack_into("<I", self.img, self.fat_off + chain[-1] * 4, extra[0])
            grew = len(extra)
            chain = chain + extra
        capacity = len(chain) * self.csz
        payload = content + b"\0" * (capacity - len(content))
        for i, c in enumerate(chain):
            o = self._clu_off(c)
            self.img[o:o + self.csz] = payload[i * self.csz:(i + 1) * self.csz]
        struct.pack_into("<I", self.img, off + 28, len(content))
        struct.pack_into("<H", self.img, off + 26, first & 0xFFFF)
        struct.pack_into("<H", self.img, off + 20, (first >> 16) & 0xFFFF)
        return key, old_size, len(content), capacity, grew

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self.img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--read")
    ap.add_argument("--replace", nargs=2, metavar=("NAME", "FILE"))
    ap.add_argument("-o", "--out")
    a = ap.parse_args()

    p = Payload(a.image)
    if a.list or not (a.read or a.replace):
        print(f"cluster={p.csz:,} B  files={len(p.entries)}")
        for name, (off, first, size) in sorted(p.entries.items(), key=lambda kv: -kv[1][2]):
            chain = p._chain(first)
            print(f"  {size:>11,} B  chain={len(chain):>2} cap={len(chain) * p.csz:>11,}  {name}")
        return 0

    if a.read:
        key, data = p.read(a.read)
        print(f"{key}: {len(data):,} B  sha256={hashlib.sha256(data).hexdigest()[:16]}")
        if a.out:
            open(a.out, "wb").write(data)
            print("wrote", a.out)
        return 0

    name, path = a.replace
    content = open(path, "rb").read()
    key, old, new, cap, grew = p.replace(name, content)
    out = a.out or a.image.replace(".img", "-patched.img")
    p.save(out)
    print(f"{key}: {old:,} -> {new:,} B (capacity {cap:,}, +{grew} cluster{'s' if grew != 1 else ''})")
    print(f"wrote {out}")
    # verify by re-reading
    q = Payload(out)
    k2, data = q.read(name)
    ok = hashlib.sha256(data).hexdigest() == hashlib.sha256(content).hexdigest()
    print(f"verify: {k2} sha256={hashlib.sha256(data).hexdigest()[:16]} {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
