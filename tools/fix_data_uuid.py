#!/usr/bin/env python3
"""把 Data 卷 UUID 一次改全：种子里的二进制副本 + 内核里的 ASCII 锚点。

背景
----
XNU_OWNER_DATA_VOLUME_UUID 是编译期宏，每个没注入它的 CI 构建都会生成新的
随机 UUID。UUID 在一个 boot 镜像里以两种形态存在，必须同时改：

  1. KERNEL~1.MAC 里紧随 "NATIVE_OWNER_DATA provider=ram uuid=%s" 的
     36 字节 ASCII 字符串（1 处）；
  2. SCRATCH（DBRDISK1 数据种子）里的 16 字节大端二进制，卷 UUID 在种子内
     重复出现（实测 7 处，分布在 APFS 头与 persist 记录里）。

只改第 1 处会得到 "内核要 0EBB496E、种子写 AB60B93C" 的组合，UniXNU 的
Boot 动作会返回 EFI_COMPROMISED_DATA（菜单上显示 Compromised Data），
内核根本没有启动。

用法
----
  python fix_data_uuid.py <in.img> --show
  python fix_data_uuid.py <in.img> <out.img> --to 0EBB496E-FB09-4223-991E-22007B6FACD1
  python fix_data_uuid.py <img> --verify            # 只检查三处是否一致
"""
import argparse
import collections
import re
import sys
import uuid

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from patch_payload_file import Payload

UUID_RE = re.compile(rb"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")
ANCHOR = b"NATIVE_OWNER_DATA provider=ram uuid=%s"
DEVICE = "0EBB496E-FB09-4223-991E-22007B6FACD1"

KNOWN = {
    DEVICE: "设备当前 Data（build 188 建的）",
    "ACDE09DC-D234-467B-BA4B-DAE346B46655": "gpu72 / stage12 CI 默认值",
    "76CF6F75-D234-4024-AD75-8EC0884F9A28": "feat/ios16-gpu CI 默认值",
}


def kernel_uuid(mac):
    i = mac.find(ANCHOR)
    if i < 0:
        return None
    m = UUID_RE.search(mac, i, i + len(ANCHOR) + 64)
    return (m.start(), m.group().decode()) if m else None


def seed_uuid(seed, hint=None):
    """在种子里找卷 UUID，只按已知值查，不做统计猜测。

    优先级：内核里的值 → 设备实盘值 → 已知的 CI 默认值。
    全都不在种子里就返回 (None, 0)，由调用方报错。绝不"取出现最多的 16 字节"：
    种子里有大量重复块（全零、块头模式），统计法会选错并把种子写坏。
    """
    order = []
    if hint:
        order.append(hint)
    order.append(DEVICE)
    order += [u for u in KNOWN if u not in order]
    for value in order:
        try:
            b = uuid.UUID(value).bytes
        except ValueError:
            continue
        n = seed.count(b)
        if n:
            return b, n
    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--to")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    p = Payload(args.src)
    _, mac = p.read("KERNEL~1.MAC")
    _, seed = p.read("SCRATC")

    k = kernel_uuid(mac)
    if not k:
        print("!! 内核里没找到 NATIVE_OWNER_DATA 锚点（这版可能不带该日志）")
        return 2
    koff, cur = k
    sb, sn = seed_uuid(seed, cur)

    print(f"镜像: {args.src}  ({len(seed):,} B 种子)")
    print(f"  内核 ASCII UUID : {cur}  @0x{koff:X}")
    if sb:
        print(f"  种子  二进制 UUID: {uuid.UUID(bytes=sb)}  ×{sn}")
    else:
        print("  种子  二进制 UUID: 未识别")

    # 种子里另有一个 48 字节零填充的 ASCII UUID 记录（20 GiB 逻辑镜像的
    # 0xa4000000 处，一段 696 KB 稀疏 extent 的开头）。它不参与启动：
    #   - UEFI 固件里 "DBRDISK" 出现 0 次，引导器根本不解析种子内容
    #   - 内核里 19 个 UUID 形态的字符串中没有它；被比对的只有锚点后那一处
    #   - 实证：所有能启动的镜像（boot-metal / boot-merge5）都是
    #     内核 0EBB496E、种子 ASCII D952AA48 —— 两者本来就不等，照样启动成功
    # 所以这里只做报告，绝不参与一致性判定，也不改写它（它在 guest 数据里）。
    import re as _re
    _ascii = _re.search(rb"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", seed)
    if _ascii:
        print(f"  种子  ASCII 记录  : {_ascii.group().decode()}  （guest 数据，不参与启动校验）")
    print(f"  设备实盘期望     : {DEVICE}")
    if cur in KNOWN:
        print(f"  （内核那处是 {KNOWN[cur]}）")

    same = bool(sb) and uuid.UUID(bytes=sb).hex == uuid.UUID(cur).hex
    print(f"  两侧是否一致     : {'是' if same else '否 ← 这种组合会 Compromised Data'}")

    if args.verify:
        verdict = "一致" if (same and cur == DEVICE) else "不一致"
        print(f"\n--verify: 与设备值{verdict}")
        return 0 if (same and cur == DEVICE) else 4

    if args.show:
        print("\n--show：未写文件")
        return 0

    if not args.to:
        print("!! 需要 --to <UUID>")
        return 3
    target = args.to.strip().upper()
    if len(target) != 36 or not UUID_RE.fullmatch(target.encode()):
        print(f"!! 目标 UUID 格式不对: {target}")
        return 3

    tb = uuid.UUID(target).bytes
    new_mac = bytearray(mac)
    new_mac[koff:koff + 36] = target.encode()
    new_seed = seed.replace(sb, tb) if sb else seed
    replaced = seed.count(sb) if sb else 0
    if uuid.UUID(bytes=sb).hex == uuid.UUID(target).hex if sb else False:
        replaced = 0

    p.replace("KERNEL~1.MAC", bytes(new_mac), grow=False)
    if replaced:
        p.replace("SCRATC", new_seed, grow=False)
    out = args.dst or args.src.replace(".img", "-uuid.img")
    p.save(out)
    print(f"\n已写出 {out}")
    print(f"  内核: {cur} -> {target}")
    print(f"  种子: {replaced} 处二进制 -> {target}")

    q = Payload(out)
    _, m2 = q.read("KERNEL~1.MAC")
    _, s2 = q.read("SCRATC")
    k2 = kernel_uuid(m2)
    ok_kernel = k2 and k2[1] == target
    ok_seed = s2.count(tb) == replaced + (seed.count(tb) if sb else 0)
    print(f"回读: 内核={k2[1] if k2 else '?'}  种子×{s2.count(tb)}  "
          f"{'通过' if ok_kernel and ok_seed else '失败'}")
    return 0 if (ok_kernel and ok_seed) else 5


if __name__ == "__main__":
    sys.exit(main())
