# 备份与恢复

> 这份文档要解决的是：**动手前该备份什么、怎么备份、出事怎么回到原状。**
> 本方案会写入 boot 分区、并替换一块数据分区，所以备份不是可选项。

---

## 0. 先记住三个数字

| 项 | 值 | 说明 |
|---|---|---|
| boot 分区大小 | **128 MiB**（134,217,728 B） | 我们的原厂备份 `boot-stock.img` 正是这个大小 |
| 原厂 boot 备份 sha256 | `0614705392e38dad…` | 恢复后可比对，确认写回去的是同一份 |
| 被替换的数据分区 | **20 GiB** 逻辑容量 | 启动参数 `picasso-data-replace-20g=1` |

`boot-stock.img` 以 `ANDROID!` 开头，是**整分区转储**（不是截断过的 boot.img）。
这意味着它可以直接 `fastboot flash boot` 回去，不需要额外处理 padding。

---

## 1. 备份什么（按重要性排序）

| 优先级 | 分区 | 丢了会怎样 | 是否可再生 |
|---|---|---|---|
| **① 最高** | `persist`、`modemst1`、`modemst2` | **IMEI / 基带校准丢失，手机没信号** | ❌ 不可再生 |
| **② 高** | `boot` | 无法启动当前系统 | ✅ 可用线刷包恢复 |
| **③ 中** | `dtbo`、`vbmeta` | AVB 校验失败、启动异常 | ✅ 线刷包有 |
| ④ 低 | `super`（system/vendor） | 系统损坏 | ✅ 线刷包有 |
| ⑤ 看你 | `userdata` | 你的照片/聊天记录 | ❌ 只能靠自己备 |

**一句话**：`persist` 和 `modemst*` 一定要单独存一份，并且**存两个地方**。
其它分区坏了都能用官方线刷包救回来，这三个不行。

> 说明：不同机型的分区名可能略有差异（有的叫 `modemst1/2`，有的合并成 `fsg`）。
> 备份前先跑 `fastboot getvar all` 或看 `/dev/block/by-name/` 确认真实名字，不要照抄。

---

## 2. 怎么备份

### 方法 A：TWRP（最省事，推荐）

进 TWRP → Backup → 勾选 `Boot`、`Persist`（如果有）、`EFS/modem`（如果有）→ 备份到外置 SD 或 OTG U 盘。

优点：不用 root、不用敲命令、能直接选分区。
缺点：需要设备有可用的 TWRP。

### 方法 B：root + dd（最可控）

```bash
adb shell su -c 'ls -l /dev/block/by-name/'          # 先确认真实分区名
adb shell su -c 'dd if=/dev/block/by-name/boot of=/sdcard/boot-stock.img'
adb pull /sdcard/boot-stock.img
```

`persist` 这类小分区同理，逐个 dump。**大分区（userdata/super）建议分块**，
否则单文件太大不好传；我们自己的 `chunks/` 目录就是这么来的（215 个分块）。

### 方法 C：fastboot（只有部分设备支持）

```bash
fastboot getvar all > device-info.txt                # 分区表、版本号，务必保存
fastboot fetch boot boot-stock.img                   # 支持 fetch 的机型才行
```

多数小米机型的 fastboot **不支持 `fetch`**，会报 unknown command。不支持就用 A 或 B。

### 无论用哪种，都额外保存这两样

```bash
fastboot getvar all > device-info.txt     # 分区表 / anti-rollback 版本 / 当前 slot
fastboot getvar current-slot              # 有没有 A/B，决定后面命令带不带 _a/_b
```

`device-info.txt` 看起来没用，但恢复时"这个分区原来多大、叫什么、是不是 A/B"全靠它。

---

## 3. 怎么恢复

### 3.1 只坏 boot（最常见）

```bash
fastboot flash boot boot-stock.img
fastboot reboot
```

如果设备是 A/B 分区，要指明槽位：`fastboot flash boot_a boot-stock.img`。
先看 `fastboot getvar current-slot` 的输出再决定。

### 3.2 数据分区被替换过，原系统起不来

iOS 的持久化层替换过那块 20 GiB 分区，原系统首次启动可能因为文件系统不认识而卡住。
两种处理：

```bash
fastboot -w                 # 清 userdata（会丢数据，先确认没重要东西）
fastboot reboot
```

或者直接用官方线刷包做一次 `clean all`（见 3.3）。

### 3.3 整机回到出厂

1. 下载对应机型的**官方 fastboot 线刷包**（不是 recovery 卡刷包）
2. 解压，Windows 下运行 `flash_all.bat`（会清数据），Linux/macOS 下 `./flash_all.sh`
3. 首次开机较慢，耐心等

这条路径能修好绝大多数"刷坏了"的情况，前提是 **bootloader 还能进 fastboot**。

---

## 4. 恢复后怎么确认真的恢复了

```bash
# 1) 回读分区，和备份比对
adb shell su -c 'dd if=/dev/block/by-name/boot of=/sdcard/check.img'
adb pull /sdcard/check.img
sha256sum check.img boot-stock.img      # 两者应完全一致
```

Windows 下用：

```powershell
Get-FileHash .\check.img, .\boot-stock.img -Algorithm SHA256
```

不一致就说明没刷进去（或刷错槽位），别急着重启，先查 `current-slot`。

---

## 5. 出事了按这个顺序处理

| 现象 | 处置 |
|---|---|
| 卡在开机画面 / 一直重启 | 进 fastboot → `fastboot flash boot boot-stock.img` |
| 进不了 fastboot，但插电脑有反应 | 试 EDL 模式（高通 9008）+ 官方线刷工具；这需要机型对应的 firehose 文件 |
| 能进 fastboot，但刷 boot 也起不来 | 用官方线刷包整机恢复（3.3） |
| 没信号 / IMEI 变成 0 | `persist` / `modemst*` 丢过——只能刷回你**事先**备份的那份。这就是为什么 ① 优先级最高 |
| 存储变小 / 分区表异常 | 线刷包的 `flash_all` 会重建分区表 |

**进 fastboot 的通用方式**：关机 → 按住 **音量下 + 电源** → 出现 FASTBOOT 字样松手。
进不去就先关机再试，别在系统里点重启。

---

## 6. 关于本项目的两个提醒

1. **`fastboot boot` 不写分区，但它救不了你**。它只能让"这一次启动"用内存里的镜像；
   设备上已经写入的 boot 镜像和数据分区不会因此复原。
2. **备份要在第一次 `fastboot flash` 之前做**。我们自己的 `boot-stock.img` 就是在那之前 dump 的，
   所以现在还能随时回去。顺序反了就没有第二份了。

---

## 7. 我们自己的备份清单（供参照）

```text
D:/k30i-backup/
  boot-stock.img      134,217,728 B   sha256 0614705392e38dad…   原厂 boot 整分区
  chunks/             215 个分块      （大分区按块切分，便于传输与校验）
```

这份清单只是示例：**你的设备 UUID、分区大小、sha256 都会不一样**，
照抄文件名可以，照抄数值没有意义。
