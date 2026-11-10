#!/usr/bin/env python3
"""boot 前强制自检：确认镜像可以安全地 fastboot boot 到这台设备上。

在设备上，Data 卷是按 UUID 匹配的（见 feature-roadmap.md 的 D1）：
内核里的 XNU_OWNER_DATA_VOLUME_UUID 必须等于设备数据卷实盘那个值，
否则 NATIVE_OWNER_DATA 会 stage=0 error=2 → panic。

本脚本做四类检查，任一失败即返回非零，禁止继续 boot：

  1. DATA 卷 UUID == 设备实盘值，且全镜像内只出现 1 次
  2. 任何已知的错误 UUID 出现 0 次（防止误用其他 CI 构建的产物）
  3. boot.img / kernel.macho 头部完好（大小、ANDROID! 魔数、段长）
  4. 与 known-good 基线的比较（可选，--baseline）

用法：
  python preboot_check.py <boot.img>                    # 四项全查
  python preboot_check.py <boot.img> --record-baseline  # 首次运行，记录基线
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

# 设备数据卷实盘 UUID（来自 build 188 内核常量 + 真机日志）
DEVICE_DATA_UUID = "0EBB496E-FB09-4223-991E-22007B6FACD1"

# 已知的其他构建默认值 —— 出现在镜像里就说明拿错了产物
BAD_UUIDS = {
    "ACDE09DC-D234-467B-BA4B-DAE346B46655": "stage12 / gpu72 CI 默认值",
    "76CF6F75-D234-4024-AD75-8EC0884F9A28": "feat/ios16-gpu CI 默认值",
}

UUID_RE = re.compile(rb"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")
BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines")


def sha256_file(path, chunk=32 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def find_data_uuid(data):
    anchor = b"NATIVE_OWNER_DATA provider=ram uuid=%s"
    i = data.find(anchor)
    if i < 0:
        return None
    m = UUID_RE.search(data, i, i + len(anchor) + 64)
    return (m.start(), m.group().decode()) if m else None


def check_boot_header(data):
    """ANDROID! v0/v1/v2 头的关键字段。"""
    if len(data) < 4096:
        return False, "文件小于 4096 字节"
    if data[:8] != b"ANDROID!":
        return False, f"魔数不是 ANDROID!（实际 {data[:8]!r}）"
    kernel_size, = struct.unpack_from("<I", data, 8)
    ramdisk_size, = struct.unpack_from("<I", data, 16)
    page_size, = struct.unpack_from("<I", data, 36)
    if page_size not in (2048, 4096, 16384, 65536):
        return False, f"page_size 异常: {page_size}"
    if kernel_size == 0 or ramdisk_size == 0:
        return False, f"kernel_size={kernel_size} ramdisk_size={ramdisk_size}"
    need = page_size + ((kernel_size + page_size - 1) // page_size) * page_size \
        + ((ramdisk_size + page_size - 1) // page_size) * page_size
    if len(data) < need:
        return False, f"文件 {len(data):,} < 头部声明的 {need:,}"
    return True, f"kernel={kernel_size:,} ramdisk={ramdisk_size:,} page={page_size} 文件={len(data):,}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--record-baseline", action="store_true")
    ap.add_argument("--baseline", default=None, help="基线名（默认用文件名）")
    args = ap.parse_args()

    if not os.path.exists(args.image):
        print(f"!! 文件不存在: {args.image}")
        return 2

    size = os.path.getsize(args.image)
    with open(args.image, "rb") as f:
        data = f.read()

    print(f"镜像: {args.image}")
    print(f"大小: {size:,} bytes")
    print("=" * 62)

    failures = []

    # --- 1. DATA 卷 UUID ---
    hit = find_data_uuid(data)
    if hit is None:
        print("[FAIL] 找不到 NATIVE_OWNER_DATA UUID 锚点（这版内核可能不带该日志）")
        failures.append("uuid-anchor-missing")
    else:
        off, current = hit
        count = data.count(current.encode())
        ok = current == DEVICE_DATA_UUID
        print(f"[{'OK  ' if ok else 'FAIL'}] DATA 卷 UUID = {current} @0x{off:X}")
        print(f"       期望设备实盘值 = {DEVICE_DATA_UUID}")
        if not ok:
            print(f"       → 用 fix_data_uuid.py --to {DEVICE_DATA_UUID} 修正")
            failures.append("uuid-mismatch")
        if ok and count != 1:
            print(f"[FAIL] 目标 UUID 出现 {count} 次（期望 1 次）")
            failures.append("uuid-count")

    # --- 2. 错误 UUID 必须为 0 ---
    for bad, desc in BAD_UUIDS.items():
        n = data.count(bad.encode())
        if n:
            print(f"[FAIL] 发现错误 UUID {bad} × {n} （{desc}）")
            failures.append(f"bad-uuid:{bad[:8]}")
        else:
            print(f"[OK  ] 未发现错误 UUID {bad[:8]}…（{desc}）")

    # --- 3. boot.img 头 ---
    ok, detail = check_boot_header(data)
    print(f"[{'OK  ' if ok else 'FAIL'}] boot.img 头: {detail}")
    if not ok:
        failures.append("boot-header")

    # --- 4. 基线比较 ---
    name = args.baseline or os.path.basename(args.image)
    bpath = os.path.join(BASELINE_DIR, name + ".json")
    digest = sha256_file(args.image)

    if args.record_baseline:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        rec = {"image": os.path.abspath(args.image), "size": size, "sha256": digest,
               "device_data_uuid": DEVICE_DATA_UUID}
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)
        print(f"[OK  ] 已记录基线 → {bpath}")
        print(f"       sha256 = {digest}")
    elif os.path.exists(bpath):
        rec = json.load(open(bpath, encoding="utf-8"))
        same = rec["sha256"] == digest
        print(f"[{'OK  ' if same else 'WARN'}] 与基线比对: "
              f"{'完全一致' if same else '内容已变化'}（基线 {rec['sha256'][:16]}…）")
        if not same:
            print(f"       当前 {digest[:16]}…  基线的 UUID 期望值 = {rec['device_data_uuid']}")
    else:
        print(f"[INFO] 无基线记录（首次运行可加 --record-baseline）")
        print(f"       当前 sha256 = {digest}")

    print("=" * 62)
    if failures:
        print(f"禁止 boot：{len(failures)} 项未通过 → {', '.join(failures)}")
        return 1
    print("全部通过，可以 fastboot boot。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
