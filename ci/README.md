# CI 配置

`compose-bootimg.example.yml` 是上游 `.github/workflows/compose-bootimg.yml` 的可复用版本，
**只有两处改动**，都写在文件头的注释里。

## 两处改动

### 1. 补回 `sheng-gpu-metal=1`

在 `Tune board.toml` 这一步的参数列表里。

缺了它，`PicassoDisplay` 不会撤掉 CPU 加速器桩（`PicassoCpuAccelerator`，故意不提供
`MetalPluginName`），CoreAnimation 就停在软件合成路径。症状是：

- 桌面能起、毛玻璃也有（CPU 算的），**看起来是好的，只是慢**
- `PICASSO_ACCEL published=1`
- `IOUC ... failed MACF ... backboardd` 每秒几百行
- `PICASSO_GPU submits` 整场只有 1 次
- `PICASSO_DPU_FRAME period_us` ≈ 50000（20 Hz）

加上之后：`published=0`、`plugin type=4147`、`PICASSO_DISPLAY copy` 归零、16.3–17.6 ms/帧。

**启动参数总长度上限 1000 字节（含结尾 NUL）**，加之前先算：

```bash
python3 -c "a='sheng-gpu=4 sheng-gpu-metal=1 ...'.split(); print(sum(len(x)+1 for x in a), '/1000')"
```

### 2. 新增 `Merge owner trust caches` 步骤

位置在 `Fetch the published GPU addon pair` 与 `Build kernel + payload` 之间，
并把构建命令的 `--owner-trust-cache` 指向合并后的 bundle。

原因：owner trust 必须**一个库一个模块**，且模块 uuid 由该库的 cdhash 派生

```
module_uuid = uuid5(NAMESPACE_URL, "picasso:statusbar:" + cdhash_hex)
```

把 GPU addon 和 SpringBoard 注入库塞进同一个模块（uuid 只能对应其中一个），
启动器会在 Boot 菜单直接返回 `EFI_COMPROMISED_DATA`，连内核都进不去。

合并用仓库根目录的 `tools/merge_trust.py`，它按模块去重、保留每个模块自己的 uuid。

## 用法

```bash
# 1) 复制到你的 fork
mkdir -p .github/workflows
cp ci/compose-bootimg.example.yml .github/workflows/compose-bootimg.yml

# 2) 确认 Settings → Actions → Allow all actions 已启用

# 3) 触发
gh workflow run compose-bootimg.yml -f fast=true -f create_data_seed=true
```

第一次必须 `create_data_seed=true`：复用链需要仓库里已存在可复用的输入包，
从来没有成功过就复用不了。之后可以改成 `false` 省时间。

## 已知的两个坑

| 现象 | 原因 |
|---|---|
| `Compose artifact has no reusable iOS16 input bundle` | 复用逻辑扫到了**失败运行**上传的 artifact（boot 目录为空）。上游脚本已改成跳过，自己改过的话注意 |
| 构建成功但刷上去很卡 | 参数里少了 `sheng-gpu-metal=1` |

## 注意

这份 yml 会下载并打包 iOS 的 userdata 与内核，**产物不要公开分发**（见仓库根 README §八）。
