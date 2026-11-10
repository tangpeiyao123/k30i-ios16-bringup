# 在别人的 K30 上跑起来

> 本文解决一个最容易卡住的问题：**我们的 UUID、种子、参数都绑在这一台机器上，你直接刷我们的镜像一定失败。**
> 正确做法是用你自己的构建产物。

---

## 0. 前置条件（必读）

| 条件 | 是否必须 | 说明 |
|---|---|---|
| **bootloader 已解锁** | ✅ **必须** | 没解锁时 `fastboot boot` 会直接被拒。小米机型需走官方解锁流程（绑定账号 + 等待期），解锁会**清空设备数据**，先备份 |
| USB 调试 / fastboot 工具 | ✅ 必须 | platform-tools；Windows 需要能枚举到 fastboot 设备 |
| **root** | ❌ **不需要** | 整个流程没有任何一步需要 root，也不改 Android 系统分区 |
| TWRP / 第三方 recovery | ❌ 当前流程不需要 | 我们排障早期用过，但现在的流程只依赖 fastboot |
| **原厂 boot 镜像备份** | ✅ **必须** | 这是回退的唯一手段。设备一旦写入 iOS 引导镜像，Android 就不再启动。做法见 [`docs/BACKUP.md`](BACKUP.md) |
| 电量 | 建议 ≥ 3.8 V | 一次启动 + 抓日志会持续耗电，低于 3.75 V 容易中途断电 |

**不需要 root 这件事值得强调**：所有操作都在 fastboot 层完成，Android 侧一个分区都不写。

---

## 0.1 这不是双系统，也不是「一次性实验环境」

这一点必须在动手前说清楚，否则预期会完全错位。

- **不是双系统**：没有开机菜单，没有「Android / iOS 二选一」。boot 分区写入 iOS 引导镜像后，
  开机直接进 iOS。
- **不是重启就回原系统**：数据是**持久化**的。参数 `sheng-data-persist=2`、
  `picasso-data-init-blank=1`、`picasso-data-replace-20g=1` 就是这套机制；
  开机画面上那行 `DATA st= hdr= gpt=xx/xx lba=… pres= ld= wr= sy= er=`
  是它的运行统计（`wr` 写入、`sy` 同步、`er` 错误），`gpt`/`lba` 说明它按 GPT 定位到真实分区。

```text
开机 → iOS（不是原系统）
iOS 里的数据 → 写进那个 20 GiB 分区，重启后还在
Android → 不再是可启动系统
```

所以真实形态是「**把原系统替换掉的单系统安装**」：

| | |
|---|---|
| 回退 | 必须刷回原厂 boot 镜像（必要时还要处理被替换的数据分区） |
| `fastboot boot` | 只是**测试新镜像**的手段：这一次启动用内存里的镜像，不写分区 |
| 它做不到什么 | **不能把设备恢复成 Android** |

`fastboot boot` 不写分区这件事仍然成立，所以试错成本低；但别把它误解成"设备还留着原系统"。
本文档和仓库里所有实验都用 `boot` 不用 `flash`，是为了**试错时不动分区**，
不是因为设备还能回原系统。

---

## 0.2 先确认机型

| 机型 | 平台 | 能不能用这套 |
|---|---|---|
| Redmi K30 5G | `picasso` / SM7250 | ✅ 同一套 |
| Redmi K30i 5G | `picasso` / SM7250 | ✅ 同一套（本文实测机） |
| Redmi K30 4G | 不同 SoC | ❌ 不适用 |
| Redmi K30 至尊纪念版 | 天玑 1000+ | ❌ 完全不同平台 |

判据不是型号名，是设备树：`compatible` 里含 `qcom,lito`、`qcom,msm-id = <0x190>`。
`lito` 是 SM7250 的代号；SM7125 的代号是 `atoll`，两者不通用。

---

## 1. 关键：不要用别人的 UUID

我们镜像里的种子 UUID（`0EBB496E-…`）是**我们那台设备的**。你刷了只有两种结果：

- `EFI_COMPROMISED_DATA`（UEFI 直接拒）
- 或进内核后 `panic: offline scratch Data mount failed: 2`

**你自己构建出来的镜像不需要改 UUID**：CI 会把内核和种子一起编译，两边天然一致。
先跑一遍确认：

```bash
python3 tools/fix_data_uuid.py boot.img --verify
```

| 输出 | 含义 | 动作 |
|---|---|---|
| `两侧是否一致: 是` | 内核和种子对得上 | **什么都不用做**，直接 boot |
| `两侧是否一致: 否` | 你混用了不同批次的 kernel / seed | 重新用同一次构建的产物 |

---

## 2. 什么时候才需要 `fix_data_uuid`

**正常情况下不需要。** 同一次构建产出的 kernel 和 seed，UUID 天然一致。

只有一种情况需要动它：**你混用了不同批次的产物**——比如只重编了内核、却沿用上一次的种子。
这时两侧对不上，表现为：

- `EFI_COMPROMISED_DATA`（启动器层，连内核都没进）
- 或 `panic: offline scratch Data mount failed: 2`（内核层，ENOENT）

**首选做法是不要混用**：重新用同一次构建的 kernel + seed。
如果确实需要对齐（例如你手上只有一份种子），再改：

```bash
# 先看当前镜像两侧是什么值
python3 tools/fix_data_uuid.py boot.img --show

# 把内核 1 处 ASCII + 种子 7 处二进制一起改到同一次构建里的那个值
python3 tools/fix_data_uuid.py boot.img boot-fixed.img --to <目标 UUID>
```

`--show` 会把两侧都打出来，并直接告诉你「是否一致」。**目标值取同一次构建里的那一侧，
不要照抄我们的 `0EBB496E…`**——那是我们这台机器的历史值。

⚠️ 改完仍然要保持 kernel 与 seed 同源。新内核 + 旧种子即使 UUID 对齐了，
只要版本不配套，一样会 ENOENT。

---

## 3. 屏幕相关的参数需要按机型改

我们的状态栏补偿（把电池图标左移避开右上角挖孔）是硬编码的：

```objc
insets.leading  = 8.91;                                  // pt
insets.trailing += 0.5 + width * 14.5 / 69.5;            // 14.5 mm 换算成 pt
```

`14.5 mm` 是按 K30i 的挖孔位置与屏宽（69.5 mm）算的。**你的机型如果挖孔位置或屏宽不同，
这两个数要重算**，否则电池要么压在孔上、要么偏得太左。

同理，面板分辨率不同（K30i 是 1080×2400）时，`display-scale` 也要重定：
逻辑宽度 = 面板像素宽 ÷ scale。

---

## 4. 第一次启动的推荐顺序

```
1) 用你自己的一次构建：boot.img（kernel + seed 同源）
2) python3 tools/fix_data_uuid.py boot.img --verify      # 应为「一致」
3) python3 tools/preboot_check.py boot.img                # 四项门禁
4) fastboot boot boot.img                                 # 只 boot，不 flash
```

第 4 步之后看三行，确认真的起来了：

```
NATIVE_OWNER_DATA done stage=4 error=0
PICASSO_ACCEL published=0
PICASSO_GPU open: plugin type=4147 ...
```

没有 `PICASSO_ACCEL published=0` 就说明退回软件渲染了，检查启动参数里有没有
`sheng-gpu-metal=1`（见 `docs/BUILD.md` §3）。

---

## 5. 出问题时先看这个表

| 现象 | 最可能的原因 | 看哪里 |
|---|---|---|
| 停在菜单，`'Boot' returned: Compromised Data` | owner trust 里没有实际加载的那个库的 cdhash，或某模块 uuid 与内容不符 | `docs/BUILD.md` §4 |
| `panic: offline scratch Data mount failed: 2` | kernel 与 seed 不同源 | §1 |
| 桌面能起但很卡、控制台刷 `IOUC ... failed MACF` | 少了 `sheng-gpu-metal=1` | `docs/BUILD.md` §3 |
| 状态栏电池压在挖孔上 | 注入库没被信任（`signature error=1`）或补偿参数不对 | §3 |
| 桌面正常但帧率只有 ~20 | 软件合成，同上 | `docs/BUILD.md` §3 |

---

## 6. 回退怎么做

**回退不是"重启"，是"刷回去"。** 这一点务必分清（备份/恢复的完整步骤见 [`docs/BACKUP.md`](BACKUP.md)）：

| 步骤 | 命令 | 作用 |
|---|---|---|
| 1 | `fastboot flash boot <原厂 boot.img>` | 恢复 Android 的引导，设备才能再启动原系统 |
| 2 | 视情况处理数据分区 | iOS 的持久化层替换过那块 20 GiB 分区，原系统首次启动可能需要格式化 |

所以**动手前一定要把原厂 boot 分区备份出来**：

```bash
fastboot getvar partition-size:boot          # 确认分区大小
fastboot flash boot boot-stock.img          # 反过来用：恢复
```

日常试错仍然只 `fastboot boot`：它不写分区，失败最多重启一次。
但请记住磁盘上**已经**存在一套 iOS 的持久化数据；把引导刷回去不等于数据自动复原。
