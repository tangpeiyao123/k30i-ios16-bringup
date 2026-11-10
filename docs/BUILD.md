# 复刻指南

> 目标：让你从上游仓库 + 本目录的工具，构建出**你自己那份**镜像。
> 这里**不提供镜像文件**，原因见文末。

---

## 0. 前提

| 项 | 要求 |
|---|---|
| 设备 | Redmi K30i 5G / K30 5G（`picasso`，SM7250），bootloader 已解锁 |
| 主机 | Linux / macOS / Windows 均可；Windows 下用 Git Bash 或 WSL 更顺 |
| 工具 | `fastboot`（platform-tools）、`python3`、`git` |
| iOS 输入 | 自备 iOS 16.7.16 IPSW（上游脚本会从中取 userdata），本项目不分发 |

**先明确一件事**：所有实验都用 `fastboot boot`，**不要 `flash`**。这样最坏情况是硬重启回原系统，
分区和数据都还在。只有你确认某个镜像稳定之后，才考虑写进 boot 分区。

---

## 1. 代码来源

```bash
git clone https://github.com/YinCQL/UniXNU-picasso
cd UniXNU-picasso
git checkout feat/ios16-gpu
```

本项目是在这个分支上做设备适配与排障，没有另立内核树。

本文提到的那几处内核侧改动（SCM 只读探针、`sheng-scm` 开关）在我们 fork 的 `gpu72` 分支上，
可以直接对照：

```
https://github.com/tangpeiyao123/UniXNU-picasso/tree/gpu72
  drivers/NativeWlan/ScmProbe.c          SCM/TZ 只读探针（原始寄存器 + PSCI 对照组）
  package/PicassoDisplay.cpp             加速器桩与 display-scale 策略
  package/picasso-springboard/           SpringBoard 注入库（状态栏补偿 + 屏幕参数打印）
```

---

## 2. 构建

上游自带 GitHub Actions 工作流（`.github/workflows/compose-bootimg.yml`）。触发一次构建：

```bash
gh workflow run compose-bootimg.yml -f fast=true -f create_data_seed=true
```

产物是一个 artifact，内含 `boot/boot.img`。

**构建时容易踩的两个坑**：

1. **`create_data_seed=false` 需要仓库里存在可复用的输入包**。如果从来没有成功过一次
   “新建 seed”，复用链是断的，会直接失败。第一次构建请用 `true`。
2. **失败运行也会上传 artifact**（boot 目录为空）。如果复用逻辑把这种 artifact 当成输入包，
   会报 `Compose artifact has no reusable iOS16 input bundle`。上游脚本已修，但自己改过的话注意这条。

---

## 3. 启动参数（少一个就退回软件渲染）

```
sheng-gpu-metal=1       # 撤掉 CPU 加速器桩，CoreAnimation 才会用 GPU
sheng-gpu-addon=1       # 加载 GPU addon
sheng-dpu-scanout=2     # 零拷贝直出
sheng-gpu=4
```

**参数总长度有上限（1000 字节，含结尾 NUL）**，超了会被截断。加参数前先算一下预算：

```bash
python3 - <<'EOF'
args = "sheng-gpu=4 sheng-gpu-metal=1 ...".split()
print(sum(len(a) + 1 for a in args), "/ 1000")
EOF
```

判断“有没有真上 GPU”，看这三行，不要看画面：

```
PICASSO_ACCEL published=0                 # 桩已撤
PICASSO_GPU open: plugin type=4147 ...    # CA 绑到真加速器
PICASSO_DISPLAY copy                      # 应为 0 行
```

---

## 4. owner trust cache：一个库一个模块

bundle 结构：`u32 count` + `u32 offset[count]` + 若干模块；
模块：`u32 type` + `u8 uuid[16]` + `u32 entries` + 24 字节条目（`cdhash[20]` + `hash_type` + `flags[3]`）。

**模块 uuid 必须由它装载的那条 cdhash 派生**：

```
module_uuid = uuid5(NAMESPACE_URL, "picasso:statusbar:" + cdhash_hex)
```

后果：

- 两个不同的库塞进同一个模块 → 启动器返回 `EFI_COMPROMISED_DATA`（连内核都进不去）
- 模块**数量**不是限制，5 个模块只要是“一库一模块”就能启动

工具：

```bash
# 看一份 bundle 的模块构成
python3 tools/merge_trust.py --show gpu-trust.bundle

# 合并两份（去重、保持每模块自洽）
python3 tools/merge_trust.py --out merged.bundle --bundle a.bundle --bundle b.bundle

# 在已有镜像的 trust 上追加一个库的 cdhash（uuid 自动按上面的规则派生）
python3 tools/make_trust_bundle.py boot.img out.bundle <cdhash_hex> [<cdhash_hex> ...]
```

**关键提醒**：设备上真正被加载的是数据卷里那份库，不是种子里的副本
（种子里的副本通常没有 `LC_CODE_SIGNATURE`）。所以 trust 里要放**实际会加载的那个 cdhash**；
不确定时，把新旧两条都放进去最省事。

---

## 5. 卷 UUID：两个地方必须同时改

| 位置 | 形态 |
|---|---|
| 内核 | 一处 ASCII 字符串（锚点：`NATIVE_OWNER_DATA provider=ram uuid=`） |
| 种子 | **7 份**大端 16 字节副本 |

只改内核那一处 → `EFI_COMPROMISED_DATA`。

```bash
# 看两侧是否一致（并显示种子里的 ASCII 记录，它不参与校验）
python3 tools/fix_data_uuid.py boot.img --verify

# 同时改内核 1 处 + 种子 7 处
python3 tools/fix_data_uuid.py boot.img boot-fixed.img --to <你的设备 UUID>
```

另外两条容易踩的：

- **kernel 与 seed 必须同源**。新内核 + 旧种子 → `panic: offline scratch Data mount failed: 2`（ENOENT）。
- 种子里 `0xa4000000` 处还有一个 48 字节 ASCII UUID，**它不参与启动校验**（UEFI 里连 `DBRDISK`
  这个串都没有）。能启动的镜像在这一处从来都是另一个值，不要被它带偏。

---

## 6. boot 前的门禁

```bash
python3 tools/preboot_check.py boot-fixed.img
```

四项：卷 UUID、两个已知错误 UUID 的残留、boot 头字段、sha256 基线。任何一项不过就返回非零——
**把它当强制开关，不要凭记忆判断**。

---

## 7. 抓日志 / 判读

Windows 下串口是 `COMx`，Linux 下 `/dev/ttyACMx`，115200 8N1。内核起来之后才有输出
（UEFI 阶段没有）。

```bash
# 帧率：唯一可靠的口径，不要用 eagl f<N>（那是采样打印）
grep -o "PICASSO_DPU_FRAME n=[0-9]* period_us=[0-9]*" console.log | tail -20
```

`period_us` 中位 ~18000（≈55 fps），稳态 16300–17600（57–61 fps）。

`tools/gate0_test.py` 是一个把“预检 → boot → 抓日志 → 判读”串起来的例子，可以照它改：

```bash
FASTBOOT=/path/to/fastboot python3 tools/gate0_test.py --image boot-fixed.img
```

---

## 8. 为什么不提供镜像

boot 镜像里有两样东西不适合公开分发：

| 文件 | 内容 | 问题 |
|---|---|---|
| `SCRATC~1.IMG` | iOS 数据盘内容（种子） | 含 Apple 的用户态文件，属版权材料 |
| `KERNEL~1.TRU` | Apple 二进制的 cdhash 白名单 | 分发它等于分发一套签名绕过构件 |

所以这里只提供**能自己构建出同样结果**的工具与配方。你按上面的步骤做，产出的是你自己那份镜像，
也只应该用在你自己的设备上。

工具沿用上游的 BSD-2-Clause-Patent 许可。
