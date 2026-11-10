# 在别人的 K30 上跑起来

> 本文解决一个最容易卡住的问题：**我们的 UUID、种子、参数都绑在这一台机器上，你直接刷我们的镜像一定失败。**
> 正确做法是用你自己的构建产物。

---

## 0. 先确认机型

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

只有一种情况：**你想复用设备上已经装好的数据盘**（比如上一次刷进去的系统、里面有你的 App 数据）。

那时内核里的 UUID 必须等于那个数据卷的 UUID。做法：

```bash
# 先看当前镜像两侧是什么值
python3 tools/fix_data_uuid.py boot.img --show

# 改到目标值（内核 1 处 ASCII + 种子 7 处二进制会一起改）
python3 tools/fix_data_uuid.py boot.img boot-fixed.img --to <目标 UUID>
```

目标 UUID 从哪来：**上一次能启动的那台设备**的控制台日志里有一行

```
NATIVE_OWNER_DATA provider=ram uuid=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
```

这行打的是**内核期望值**。如果它和数据盘不一致，启动就会失败；所以对一台能正常启动的设备，
这行就是那个数据卷的 UUID。

⚠️ 换 UUID 之后，**种子也要跟着来自同一次构建**。新内核 + 旧种子 = ENOENT panic，
这跟 UUID 是两回事。

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

## 6. 别忘了回退

全程 `fastboot boot`，不要 `flash`。最坏情况按住电源 10 秒硬重启，
分区还是原来的系统，数据不动。

只有当你确认某个镜像稳定（连续多次启动没问题）之后，才考虑写进 boot 分区；
写之前把原厂 boot 分区备份出来。
