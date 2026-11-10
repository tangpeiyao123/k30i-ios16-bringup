#!/usr/bin/env python3
"""构造 owner trust bundle：一个模块 = 一个库（uuid5 由 cdhash 派生）。

规则来自 scripts/picasso_ui_trust.py:83
    identifier = uuid5(NAMESPACE_URL, 'picasso:statusbar:' + cdhash)
把两条 cdhash 塞进同一个模块会让 uuid 与内容矛盾，UEFI 判定
Compromised Data。所以合并多份注入库时必须各占一个模块。
"""
import hashlib
import struct
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch_payload_file import Payload          # noqa: E402
from merge_trust import parse_bundle           # noqa: E402


def pack_module(uid_bytes, entries):
    body = b''.join(c + bytes([ht]) + bytes(fl) for c, ht, fl in entries)
    return struct.pack('<I', 2) + uid_bytes + struct.pack('<I', len(entries)) + body


def sb_uuid(cdhash_hex):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'picasso:statusbar:' + cdhash_hex)


def main():
    base_img = sys.argv[1]
    out_bin = sys.argv[2]
    extra = sys.argv[3:]

    _, tru = Payload(base_img).read('KERNEL~1.TRU')
    mods = parse_bundle(tru, 'm')
    blobs = [pack_module(m['uuid'], m['entries']) for m in mods]
    present = {c.hex() for m in mods for c, _, _ in m['entries']}
    print('底座模块:', [(m['uuid'].hex()[:8], len(m['entries'])) for m in mods])

    for h in extra:
        if h in present:
            print('  已存在，跳过', h[:12])
            continue
        ident = sb_uuid(h)
        blobs.append(pack_module(ident.bytes, [(bytes.fromhex(h), 2, b'\x00\x00\x00')]))
        print(f'  追加模块 {ident.hex[:8]}  cdhash={h[:12]}')

    n = len(blobs)
    cur = 4 + 4 * n
    offs = []
    for b in blobs:
        offs.append(cur)
        cur += len(b)
    bundle = struct.pack('<I', n) + b''.join(struct.pack('<I', o) for o in offs) + b''.join(blobs)
    open(out_bin, 'wb').write(bundle)

    print(f'\n{n} 模块  {len(bundle):,} B  sha256={hashlib.sha256(bundle).hexdigest()[:12]}')
    for m in parse_bundle(bundle, 'v'):
        ok = len(m['entries']) != 1 or m['uuid'].hex() == sb_uuid(m['entries'][0][0].hex()).hex
        print(f'  {m["uuid"].hex()[:8]} entries={len(m["entries"])}  uuid 自洽={ok}')


if __name__ == '__main__':
    main()
