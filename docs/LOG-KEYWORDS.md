# 日志关键字对照表

> 全部是串口控制台上能 grep 到的原文（前缀 `PICASSO_` 是本项目的驱动/服务打的）。
> 用法：出问题时不必读整份日志，按这张表逐条 grep。

---

## 1. 启动与数据卷

| 关键字 | 含义 | 正常值 |
|---|---|---|
| `NATIVE_OWNER_DATA provider=ram uuid=` | 内核期望的数据卷 UUID | 与种子里的 7 份一致 |
| `NATIVE_OWNER_DATA stage=1 selected=` | 选中了哪个分区 | 有具体分区号 |
| `NATIVE_OWNER_DATA mount returned error=` | 挂载结果 | **0** |
| `NATIVE_OWNER_DATA done stage=4 error=0` | 四阶段全部完成 | 出现这一行才算过 |
| `panic: offline scratch Data mount failed:` | 内核与种子不同源 | 不应出现 |

## 2. 显示与 GPU

| 关键字 | 含义 | 正常值 |
|---|---|---|
| `PICASSO_DISPLAY mapped ... scale=` | 面板模式与 UI 缩放 | `scale=3`（1080 宽 → 360 pt） |
| `PICASSO_DISPLAY geometry ... size=` | 扫描输出尺寸 | `1080x2400` |
| `PICASSO_ACCEL published=` | CPU 加速器桩是否发布 | **0** ← 真上 GPU 的前提 |
| `PICASSO_GPU open: plugin type=4147` | CoreAnimation 绑定到真加速器 | 出现，且 `name=backboardd` |
| `PICASSO_DISPLAY copy` | 旧的每帧整屏拷贝 | **0 行** |
| `PICASSO_DPU_FRAME n= period_us=` | 每帧真实间隔 | 中位 ~18000 µs（55 fps），稳态 16300–17600（57–61 fps） |
| `PICASSO_DPU flips= ... fallbacks=` | 直出汇总 | `fallbacks=0`、`latch_timeouts=0` |
| `IOUC ... failed MACF ... backboardd` | 桩被安全策略拒绝 | **0 行**；每秒几百行说明在软件合成 |

> `PICASSO_DISPLAY frame ... kind=3 nonblack=0 bounds=0,0,0,0` 是**空帧**，
> 它在直出模式下也会周期性出现，不代表画面没刷。判断帧率请看 `PICASSO_DPU_FRAME period_us`。

## 3. 注入（状态栏）

| 关键字 | 含义 | 正常值 |
|---|---|---|
| `PICASSO_INJECT signature error=` | AMFI 对注入库的裁决 | **0**；`1` 表示 trust 里没有这个库的 cdhash |
| `PICASSO_INJECT mmap_policy ... policy=AMFI ... error=0` | 映射放行 | error=0 |
| `PICASSO_SBINJECT library ran: ack read` | 库的构造函数真的跑了 | 出现 |
| `PICASSO_SBINJECT statusbar metrics installed (left .. pt, down .. pt, battery shift .. mm)` | 状态栏补偿已安装 | 出现 |
| `PICASSO_SCREEN bounds=.. native=.. scale=.. nscale=.. idiom=.. machine=.. model=..` | UIKit 眼里的屏幕参数 | 用来定位布局偏差 |

**注意区分**：日志里 `cdhash: {...} is trusted` 那几行**不是**注入库的，
是别的进程在做 AMFI 查询。判断注入只看 `PICASSO_INJECT signature` 和 `PICASSO_SBINJECT`。

## 4. SCM / TrustZone（远端子系统的门）

| 关键字 | 含义 | 怎么读 |
|---|---|---|
| `PICASSO_SCM raw PSCI_VERSION ... -> x0=` | 对照组：PSCI 必然可用 | 期望 `0x10000`（PSCI 1.0）或 `0x10001` |
| `PICASSO_SCM raw SCM_GET_VERSION(1,1)... -> x0= x1=` | SCM 版本查询的原始返回 | `x1` 非 0 → 通路通 |
| `PICASSO_SCM iscall <命令> arginfo=.. pack=.. -> status= answer=` | 逐条命令的白名单查询 | `answer` 非 0 即可用 |
| `PICASSO_SCM probe end calls= version_status= pas_available_count=` | 汇总 | 见下 |

判读规则：

- PSCI 返回乱值 → **我们的内联汇编/传参有问题**，此时 SCM 的返回不可信
- PSCI 正常 + SCM 的 `x1` 非 0 → 通路通，可以进 PAS
- PSCI 正常 + SCM 的 `x1` 恒为 0、`x0` 恒为 1 → TrustZone 确实不放行 AP 的 SIP 调用

## 5. 崩溃与稳定性

| 关键字 | 含义 | 备注 |
|---|---|---|
| `EXC_CORPSE_NOTIFY` | 进程异常退出通知 | 数量受日志采样影响，**不看绝对数，看每千行密度**；健康基线约 8/千行 |
| `Failed to send exception EXC_CORPSE_NOTIFY. error code: 5` | 通知投递失败（无接收方） | 不等于每次都真崩 |
| `panic(` | 内核崩溃 | 结合前 6 行看原因 |

---

## 6. 常用的三条命令

```bash
# 真的在跑 GPU 吗
grep -E "PICASSO_ACCEL published=|PICASSO_GPU open|PICASSO_DISPLAY copy" console.log

# 帧率（唯一可靠口径）
grep -o "PICASSO_DPU_FRAME n=[0-9]* period_us=[0-9]*" console.log | tail -20

# 注入链是否完整
grep -E "PICASSO_INJECT signature|PICASSO_SBINJECT" console.log
```
