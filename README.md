# Redmi K30i 5G 跑 iOS 16：GPU 直出打通，以及四个踩坑记录

> 设备：Redmi K30i 5G（`picasso` / SM7250 / 6 GB / 解锁 bootloader）
> 系统：iOS 16.7.16（20H392）userdata，按上游 `feat/ios16-gpu` 的配方构建
> 本文只记录**有日志支撑**的事实；没跑通的部分会明确写"未定"。
> 上游项目：[`YinCQL/UniXNU-picasso`](https://github.com/YinCQL/UniXNU-picasso)

---

## 结果先说

| 项目 | 状态 |
|---|---|
| 内核启动 + 数据卷挂载 | ✅ 每次都能进桌面 |
| GPU 渲染（CoreAnimation 走 Adreno） | ✅ `plugin type=4147`，GL 后端加载并编译着色器 |
| 零拷贝直出 | ✅ `PICASSO_DISPLAY copy` = 0，`fallbacks=0`，`latch_timeouts=0` |
| 帧率 | ✅ 稳态 16.3–17.6 ms/帧 = **57–61 fps**；114 样本中位 18.2 ms（54.9 fps） |
| 状态栏注入（电池避让挖孔） | ✅ 日志 + 肉眼双重确认 |
| 键盘右侧被裁 ~45 px | ❌ 未解决（已排除两个假设） |
| 音频 / WiFi | ⏸ 卡在 SCM/PIL，探针已写好，结果未出 |

---

## 这不是双系统安装

先说清楚方案性质，否则后面所有结论都会被误读。

`fastboot boot boot.img` 的语义是**把镜像装进 RAM 启动一次，不写任何分区**。
数据卷也不是设备上的真实分区，而是由镜像内部携带的种子合成的——日志里这行是证据：

```
NATIVE_OWNER_DATA provider=ram uuid=...
NATIVE_OWNER_DATA stage=1 selected=1000009 path=/dev/disk5s1 logical_bytes=21474836480 seed_base=0
```

| | |
|---|---|
| 重启 | 回到原来的系统，没有"开机选系统"这回事 |
| iOS 里的改动 | 不落到 Android 分区，不占你的存储、不碰你的 App 数据 |
| 分区表 | 始终原样，不会留下 iOS 分区 |
| 想再进 iOS | 必须再连电脑跑一次 `fastboot boot` |

所以它现在的形态是**可重复进入的一次性实验环境**，不是可以日常使用的系统。
唯一的不可逆操作是 `fastboot flash boot`，本仓库所有流程都只用 `boot`、不用 `flash`。

前置条件：**bootloader 已解锁**（必须；解锁会清空设备数据，先备份）；**不需要 root，也不需要 TWRP**。
完整清单见 [`docs/OTHER-DEVICES.md`](docs/OTHER-DEVICES.md)。

---

## 一、从 20 fps 到 57–61 fps：差别只有一个启动参数

现象很好骗人：桌面正常、毛玻璃正常、还能滑动，只是"卡"。三条计数器才说明它在跑软件合成：

| 指标 | 软件合成 | `sheng-gpu-metal=1` |
|---|---|---|
| `PICASSO_ACCEL` | `published=1`（CPU 桩还在） | `published=0` |
| `PICASSO_GPU open` | 从不出现 | `plugin type=4147 pid=50 name=backboardd` |
| `IOUC ... failed MACF ... backboardd` | **约 226 行/秒**，占控制台 85% | 0 |
| `PICASSO_GPU submits` | 整场 `submissions=1` | 数千 |
| `PICASSO_DPU_FRAME period_us` | ~50000（20 Hz） | 16300–17600（57–61 fps） |
| `PICASSO_DISPLAY copy` | 有（每帧 10.4 MB） | **0** |

`PicassoDisplay.cpp` 里的逻辑是：默认仍然发布 CPU 加速器桩（`PicassoCpuAccelerator`，故意不提供
`MetalPluginName`），只有 `sheng-gpu-metal=1` 时才撤掉它。桩在 → CoreAnimation 停在软件路径 →
毛玻璃由 CPU 算，所以"看起来是好的，只是慢"。

**这个失败是静默的**：除了那 226 行/秒的 MACF 拒绝，没有任何显式报错。
所以判断有没有真上 GPU，第一步应该看 `PICASSO_ACCEL published=0`，而不是看画面。

---

## 二、帧率怎么量：别用 `eagl f<N>`

我一开始拿 `eagl f<N>` 的帧号差除以抓取秒数，算出 43.6 fps —— **这个方法是错的**，
那批日志是采样/限频打印的。

正确的是 `PICASSO_DPU_FRAME period_us`（每帧真实间隔）。桌面静置时的 114 个样本：

```
中位 18224 us  → 54.9 fps
稳态 16307 / 16607 / 16702 / 16909 / 17647 us  → 57–61 fps
latch_us 中位 4.8 ms，最大 8.7 ms
fallbacks=0，latch_timeouts=0
```

这和上游 #20 报的 52.0 中位 / 62.3 峰值一致（两次测量在不同 session，能对上说明方法可靠）。

---

## 三、owner trust cache：一个库一个模块，模块 uuid 由 cdhash 派生

trust bundle 的格式不复杂：`u32 count` + `u32 offset[count]` + 若干模块；模块是
`u32 type` + `u8 uuid[16]` + `u32 entries` + 24 字节的条目。

不显然的是这条规则：

```
module_uuid = uuid5(NAMESPACE_URL, "picasso:statusbar:" + cdhash_hex)
```

我在三份独立生成的模块上验证过。由此引出两个结论：

1. **把两个不同的库塞进同一个模块**（uuid 只能对应其中一个 cdhash），启动器会直接返回
   `EFI_COMPROMISED_DATA`，连内核都进不去。
2. **模块数量本身不是限制**。5 个模块（两个 Apple 基线 + GPU addon + 两个单条目库模块）
   照样能启动，只要每个模块的 uuid 和它自己的内容自洽。

这条坑让我废掉两次启动，最后才发现问题不在"模块太多"。

---

## 四、kernel 与 seed 是配对的；卷 UUID 存在两个地方

- **新内核 + 旧种子** → `panic: offline scratch Data mount failed: 2`（ENOENT）。它们是同一次构建的产物，不能交叉组合。
- **卷 UUID 存在两处**：内核里一处 ASCII 字符串，种子里 **7 份大端二进制副本**。
  只改内核那一处，结果仍是 `EFI_COMPROMISED_DATA`——内核会拿自己的字符串去比种子里的。

**但正常情况下你不需要动它**：同一次构建的 kernel + seed 两侧天然一致。
只有混用不同批次的产物时才需要对齐，正确做法优先是"别混用"。

### 一条值得记下的红鲱鱼

种子里还有一个 48 字节零填充的 ASCII UUID，位于逻辑偏移 `0xa4000000`（一段 696 KB 稀疏 extent 的开头）。
**它不参与启动校验**：UEFI 固件里连 `DBRDISK` 这个串都出现 0 次，内核里也没有它。
实证是所有能启动的镜像在这一处都是 `D952AA48…`，而内核是 `0EBB496E…`——两者从来就不等，照样启动。
我在这上面花了不少时间才确认它无关。

---

## 五、还没解决的

### 键盘右侧被裁约 45 px

现象：键盘最右一排（p / l / return）被切掉约半个键宽。
先后面向两个假设做了单变量实验，**两个都失败**：

| 实验 | 变量 | 结果 |
|---|---|---|
| ADT `model`：`iPhone10,3` → `iPhone13,1` | 布局宽度 375 pt → 360 pt（`target-type` 仍为 `D22`） | 无变化 |
| `display-scale` 3 → 2 | 逻辑宽度 360 pt → 540 pt | 无变化 |

所以它既不按 `hw.machine` 选模板，也不按逻辑宽度算。下一步是在 SpringBoard 注入里打印
`UIScreen.bounds / nativeBounds / scale / nativeScale`，看 1125 这个数到底从哪来。

### 音频 / WiFi

两者共用同一道门：ADSP 与 WCN3990 的固件必须经 **PIL / TrustZone** 鉴权才能启动，而内核里
SCM 客户端是 0（`scm_call` / `qcom_scm` / `pas_init` 字符串出现 0 次，设备树无 `qcom,scm`）。
GPU 能跑恰恰是因为它**绕开**了 PIL（ZAP 由 TZ 鉴权，本内核只做容器解析）。

已写好只读探针（`GET_VERSION` + `IS_CALL_AVAILABLE`，外加一条 `PSCI_VERSION` 对照组用于区分
"我们的内联汇编写错"和"TZ 真的拒绝"），结果未出，**暂不下结论**。

---

## 六、复现要点（命令级）

```bash
# 1. 只 boot 不 flash，永远留一条回退路
fastboot boot boot.img

# 2. 判断是否真的走了 GPU（不是看画面）
grep "PICASSO_ACCEL published="      console.log   # 期望 0
grep "PICASSO_GPU open"              console.log   # 期望 plugin type=4147
grep -c "PICASSO_DISPLAY copy"       console.log   # 期望 0

# 3. 量帧率（唯一可靠的口径）
grep -o "PICASSO_DPU_FRAME n=[0-9]* period_us=[0-9]*" console.log | tail -20
```

必备启动参数（少一个就退回软件路径）：

```
sheng-gpu-metal=1  sheng-gpu-addon=1  sheng-dpu-scanout=2
```

---

## 七、致谢

上游 `YinCQL/UniXNU-picasso` 的文档密度是这件事能在 Windows 上做起来的前提——
尤其是 trust cache 的构建脚本和 `PicassoDisplay` 的 policy 代码，上面四条里有三条是从它们里面读出来的。
`silenzio111` 在 #19/#20/#21 里的渲染路径与性能分析，直接给了我可对照的帧率基线。

本文所有数字都来自手机上的控制台日志；没有引用的地方就是没测过。

---

## 八、如何复刻

**镜像本身不提供**。boot 镜像里有两样东西不适合公开分发：

| 文件 | 内容 | 为什么不能放 |
|---|---|---|
| `SCRATC~1.IMG` | iOS 数据盘内容（种子） | 含 Apple 的用户态文件 |
| `KERNEL~1.TRU` | Apple 二进制的 cdhash 白名单 | 分发它等于分发一套签名绕过构件 |

所以这里只放**能自己构建出同样结果**的东西：

| 目标 | 位置 |
|---|---|
| 完整构建配方：参数、trust 规则、UUID 两处对齐、boot 前门禁、日志判读 | [`docs/BUILD.md`](docs/BUILD.md) |
| **在别人的 K30 上跑起来**：机型判定、UUID 怎么定、屏幕参数怎么改 | [`docs/OTHER-DEVICES.md`](docs/OTHER-DEVICES.md) |
| 日志关键字对照表（每条打印的含义与正常值） | [`docs/LOG-KEYWORDS.md`](docs/LOG-KEYWORDS.md) |
| 可直接使用的构建工作流（含本文两处关键改动） | [`ci/`](ci/) |
| 改 payload / 改 UUID / 合并 trust / 启动前自检 / 抓日志判读 的脚本 | [`tools/`](tools/) |

照着 `docs/BUILD.md` 走，用的是上游的构建脚本 + 这里的几个工具，产出的是**你自己那份**镜像，
也只应该用在你自己的设备上。
