#!/usr/bin/env python3
"""Gate-0 test runner: boot the probe image, capture the console, print the verdict.

Prepared 2026-11-10 night. The only thing it needs from the operator is a phone
sitting in fastboot; everything else (preflight, boot, capture, parsing, verdict,
report) is automated so the result cannot be read wrong at 8am.

    python gate0_test.py                 # use the overnight image
    python gate0_test.py --image X.img
    python gate0_test.py --capture-only  # skip booting, just read a live console

Verdict comes from exactly three log lines:
    PICASSO_SCM raw PSCI_VERSION ...
    PICASSO_SCM raw SCM_GET_VERSION(1,1)no-flags ...
    PICASSO_SCREEN bounds=...
"""
import argparse
import os
import shutil
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FASTBOOT = os.environ.get("FASTBOOT") or shutil.which("fastboot") or "fastboot"
DEFAULT_IMAGE = os.environ.get("GATE0_IMAGE", "boot.img")
OUT_DIR = os.environ.get("GATE0_OUT", "gate0-out")
PORT = "COM3"


class _Missing:
    """Stand-in result when the executable itself is absent (no fastboot on PATH)."""
    returncode = 127
    stdout = ""
    stderr = "executable not found"


def run(command, timeout=120, env=None):
    environment = dict(os.environ)
    environment["MSYS_NO_PATHCONV"] = "1"
    if env:
        environment.update(env)
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, env=environment)
    except FileNotFoundError:
        return _Missing()


def say(message):
    print(message, flush=True)


def preflight(image):
    say("=" * 66)
    say("门 0 测试 · 预检")
    say("=" * 66)
    problems = []

    devices = run([FASTBOOT, "devices"], timeout=30)
    online = [line for line in devices.stdout.splitlines() if "fastboot" in line]
    if online:
        say(f"  设备        : {online[0].strip()}")
    else:
        problems.append("fastboot 里没有设备（手机需进入 fastboot：音量下 + 电源）")
        say("  设备        : 未发现")

    battery = None
    try:
        value = run([FASTBOOT, "getvar", "battery-voltage"], timeout=30)
        match = re.search(r"battery-voltage:\s*(\d+)", value.stdout + value.stderr)
        if match:
            battery = int(match.group(1))
            hint = "OK" if battery >= 3750 else "偏低，建议先充到 3.8 V 以上"
            say(f"  电池        : {battery} mV  ({hint})")
    except Exception:
        say("  电池        : 读取失败")

    if not os.path.exists(image):
        problems.append(f"镜像不存在：{image}（CI 可能还没跑完，见 overnight/report.md）")
        say(f"  镜像        : 缺失")
        return problems
    size = os.path.getsize(image)
    say(f"  镜像        : {image} ({size:,} B)")

    fixed = run([sys.executable, os.path.join(ROOT, "fix_data_uuid.py"), image, "--verify"], timeout=180)
    ok_uuid = fixed.returncode == 0
    say(f"  UUID 一致性 : {'通过' if ok_uuid else '不一致 ← 会 Compromised Data'}")
    if not ok_uuid:
        problems.append("fix_data_uuid --verify 未通过")

    gate = run([sys.executable, os.path.join(ROOT, "preboot_check.py"), image], timeout=180)
    say(f"  boot 门禁   : {'通过' if gate.returncode == 0 else '未通过'}")
    if gate.returncode != 0:
        problems.append("preboot_check 未通过")

    say("")
    if problems:
        say("预检未通过：")
        for item in problems:
            say(f"  - {item}")
    else:
        say("预检全部通过，可以开始。")
    return problems


def wait_console(limit_seconds=120):
    say("")
    say("等待串口控制台（出现即表示 UEFI 放行、内核起来了）...")
    deadline = time.time() + limit_seconds
    while time.time() < deadline:
        try:
            import serial.tools.list_ports as ports
            names = [p.device for p in ports.comports()]
        except Exception:
            names = []
        if PORT in names:
            say(f"  控制台出现（{limit_seconds - int(deadline - time.time())} s）")
            return True
        time.sleep(3)
    say("  控制台没有出现 → 停在 UEFI 或内核未启动")
    return False


def capture(seconds, log_path):
    say(f"抓取 {seconds} 秒日志 ...")
    for attempt in range(1, 5):
        done = run([sys.executable, os.path.join(ROOT, "cdcraw.py"), "capture",
                    "--port", PORT, "--seconds", str(seconds)], timeout=seconds + 60)
        if done.returncode == 0 and done.stdout:
            with open(log_path, "w", encoding="utf-8", errors="replace") as handle:
                handle.write(done.stdout)
            say(f"  抓到 {len(done.stdout):,} 字节 → {log_path}")
            return True
        say(f"  第 {attempt} 次抓取失败，重试")
        time.sleep(5)
    return False


def parse(log_path):
    text = open(log_path, encoding="utf-8", errors="replace").read()

    def raw(name):
        pattern = rf"PICASSO_SCM raw {re.escape(name)}.*?-> x0=(\S+) x1=(\S+) x2=(\S+) x3=(\S+) x6=(\S+)"
        match = re.search(pattern, text)
        if not match:
            return None
        return dict(x0=match.group(1), x1=match.group(2), x2=match.group(3),
                    x3=match.group(4), x6=match.group(5))

    result = {
        "psci": raw("PSCI_VERSION"),
        "scm_flags": raw("SCM_GET_VERSION(1,1)+flags"),
        "scm_plain": raw("SCM_GET_VERSION(1,1)no-flags"),
        "screen": re.search(r"PICASSO_SCREEN [^\n]*", text),
        "status": re.search(r"PICASSO_SCM version svc=1 cmd=1 -> status=(\S+) answer=(\S+)", text),
        "probe_end": re.search(r"PICASSO_SCM probe end[^\n]*", text),
        "sbinject": len(re.findall(r"PICASSO_SBINJECT library ran", text)),
        "metrics": len(re.findall(r"statusbar metrics installed", text)),
        "gpu": len(re.findall(r"PICASSO_GPU open", text)),
        "accel": len(re.findall(r"PICASSO_ACCEL published=0", text)),
    }
    return result


def to_int(text):
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except Exception:
        return None


def verdict(parsed):
    say("")
    say("=" * 66)
    say("门 0 判读")
    say("=" * 66)

    psci = parsed["psci"]
    if not psci:
        say("  没找到 PSCI_VERSION 原始行。")
        say("  可能原因：这次构建没带探针 / sheng-scm 未开启 / 日志抓得太早。")
        return "unknown"

    psci_x0 = to_int(psci["x0"])
    say(f"  PSCI_VERSION  : x0={psci['x0']}  x1={psci['x1']}")
    psci_ok = psci_x0 in (0x00010000, 0x00010001)
    say(f"  设备树声明    : arm,psci-1.0 → 期望 0x00010000（或 0x00010001）")
    say(f"  汇编通路      : {'正确' if psci_ok else '可疑 ← 先修代码，不要往下结论'}")

    for label, key in (("GET_VERSION +flags", "scm_flags"), ("GET_VERSION 无标志", "scm_plain")):
        row = parsed[key]
        if row:
            say(f"  SCM {label:18s}: x0={row['x0']}  x1={row['x1']}  x6={row['x6']}")
        else:
            say(f"  SCM {label:18s}: 未出现")

    if not psci_ok:
        say("")
        say("  结论：内联汇编或寄存器传参有问题，SCM 的返回值目前不可信。")
        say("  下一步：修 picasso_smc_raw / 检查 clobber 列表，再测一次。")
        return "asm-broken"

    values = [row["x1"] for row in (parsed["scm_flags"], parsed["scm_plain"]) if row]
    nonzero = [v for v in values if to_int(v)]
    if nonzero:
        say("")
        say("  结论：SCM 通路是通的（TZ 在回数据，不是拒绝）。")
        say("  下一步：进入阶段 1 —— PAS_INIT_IMAGE / MEM_SETUP / AUTH_AND_RESET。")
        return "scm-ok"

    say("")
    say("  结论：汇编正确，但 SCM 对每一个调用都回 x0=1、x1=0 —— TZ 确实不放行 AP 的 SIP。")
    say("  下一步：先试 hvc #0（部分平台 SCM 挂在 EL2）；仍失败则远端子系统路线基本封死。")
    return "scm-refused"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    log_path = os.path.join(OUT_DIR, "gate0.log")

    if not args.capture_only:
        problems = preflight(args.image)
        blocking = [p for p in problems if "偏低" not in p]
        if blocking:
            say("")
            say("先解决上面的问题再测。")
            return 2

        say("")
        say("=" * 66)
        say("开始启动镜像（只 boot，不 flash）")
        say("=" * 66)
        done = run([FASTBOOT, "boot", args.image], timeout=420)
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
        for line in tail:
            say(f"  {line}")

        if not wait_console():
            say("")
            say("没有控制台：可能停在 UEFI 菜单（Compromised Data）或内核早期崩溃。")
            say("硬重启回 build 188，把屏幕拍照，明天照 report 里的建议处理。")
            return 3
        time.sleep(8)          # 让内核跑过早期初始化再开始抓

    if not capture(args.seconds, log_path):
        say("抓取失败，串口可能被占用。")
        return 4

    parsed = parse(log_path)
    result = verdict(parsed)

    say("")
    say("=" * 66)
    say("顺带核对（确认没有回归）")
    say("=" * 66)
    if parsed["screen"]:
        say(f"  {parsed['screen'].group(0)}")
    else:
        say("  PICASSO_SCREEN: 未出现（注入库可能没加载）")
    say(f"  SBINJECT library ran : {parsed['sbinject']}")
    say(f"  statusbar metrics    : {parsed['metrics']}")
    say(f"  PICASSO_GPU open     : {parsed['gpu']}")
    say(f"  PICASSO_ACCEL=0      : {parsed['accel']}")

    report = os.path.join(OUT_DIR, "gate0-report.md")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write("# 门 0 测试报告\n\n")
        handle.write(f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.write(f"- 镜像: {args.image}\n")
        handle.write(f"- 结论: **{result}**\n\n")
        handle.write("## 原始日志行\n\n```\n")
        for key in ("psci", "scm_flags", "scm_plain"):
            if parsed[key]:
                handle.write(f"{key}: {parsed[key]}\n")
        if parsed["screen"]:
            handle.write(parsed["screen"].group(0) + "\n")
        if parsed["probe_end"]:
            handle.write(parsed["probe_end"].group(0) + "\n")
        handle.write("```\n\n")
        handle.write(f"完整日志: `{log_path}`\n")
    say("")
    say(f"报告已写入 {report}")
    return 0 if result != "unknown" else 5


if __name__ == "__main__":
    sys.exit(main())
