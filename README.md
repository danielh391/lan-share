# lan-share

局域网文件传输工具，基于 asyncio TCP/UDP，支持断点续传。

## 安装

```bash
pip install -e .
```

> 需要 Python 3.11+

---

## 快速上手

### Windows 端（接收方）

```bash
lshare recv C:\Downloads\
```

启动后自动：
- 监听 TCP 51821 等待文件传入
- 每 2 秒广播 UDP beacon，供 Linux 端自动发现

可选参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `dest` | 保存目录 | 当前目录 |
| `--tcp-port N` | TCP 监听端口 | 51821 |
| `--udp-port N` | UDP 广播端口 | 51820 |
| `--auto-accept` | 不询问直接接收 | 否 |

---

### Linux 端（发送方）

```bash
# 自动发现接收方（等待 5 秒 beacon）
lshare send /path/to/file.txt

# 直接指定 IP
lshare send /path/to/file.txt --to 192.168.1.55

# 发送整个目录
lshare send /path/to/mydir/ --to 192.168.1.55
```

可选参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--to IP` | 接收方 IP（省略则自动发现） | — |
| `--tcp-port N` | 连接端口 | 51821 |
| `--udp-port N` | 发现监听端口 | 51820 |
| `--chunk-size N` | 分块大小（字节） | 65536 |
| `--timeout S` | 自动发现等待时间（秒） | 5 |

---

### 扫描局域网接收方

```bash
lshare find
lshare find --timeout 8
```

输出示例：
```
Found 2 receiver(s):
  192.168.1.55    WIN-PC    TCP:51821
  192.168.1.88    LAPTOP    TCP:51821
```

---

## 断点续传

传输中断后，**重新执行相同的 `lshare send` 命令**即可从断点继续：

```bash
# 第一次发送（中途中断）
lshare send bigfile.bin --to 192.168.1.55

# 重启接收方后，重新发送——自动续传
lshare recv C:\Downloads\ --auto-accept
lshare send bigfile.bin --to 192.168.1.55
```

接收方用 `<filename>.lshare` 记录进度，完成后自动删除并校验 SHA-256。

---

## 典型数据流

```
Linux (Sender)                      Windows (Receiver)
──────────────                      ─────────────────
                                    lshare recv C:\Downloads\
                                    [TCP :51821 监听]
                                    [UDP :51820 广播 HELLO]

lshare find ◄── HELLO beacon ──────
lshare send bigfile.bin
  ─── TCP connect ───────────────►
  ─── OFFER (id, name, size, sha256) ►
  ◄── ACCEPT (offset=0) ──────────
  ─── DATA chunks ───────────────►  写 bigfile.bin.part
  ─── DONE ──────────────────────►
                                    SHA-256 验证 → rename → bigfile.bin
```

---

## Windows 防火墙

首次运行时 Windows 可能弹出防火墙提示，允许即可。
若出现错误，手动放行端口：

```
控制面板 → Windows Defender 防火墙 → 高级设置
→ 入站规则 → 新建规则 → 端口 → TCP 51821 / UDP 51820
```

或以管理员身份运行 `lshare recv`。
