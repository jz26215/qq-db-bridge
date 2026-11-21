# 一、读 NTQQ 本地消息库

> 目标：只读地拿到结构化消息（谁、什么时候、说了什么、是否 @ 我）。

## 1. 文件与私有头

客户端把消息存在 `nt_msg.db`（路径随版本变化，一般在客户端的 `nt_db/` 目录下）。

这个文件**不是标准 SQLite**：最前面有 **1024 字节私有头**，后面才是加密的 SQLite 页。

```
[ 1024B 私有头 ][ SQLCipher 加密的 SQLite 数据库页 ... ]
```

所以要先"剥头"：保留 1024 字节之后的内容，再交给 SQLCipher 打开。

```bash
dd if=nt_msg.db of=nt_msg.stripped bs=1024 skip=1
```

## 2. 打开时 pragma 的顺序很重要

SQLCipher 的参数必须**在 `key` 之前**设好页大小，在 `key` 之后设 KDF 参数：

```sql
PRAGMA cipher_page_size = 4096;
PRAGMA key = '<16 字节密钥>';
PRAGMA kdf_iter = 4000;
PRAGMA cipher_hmac_algorithm = HMAC_SHA1;
PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;
```

顺序错、或者少一个参数，最常见的报错就是 `file is not a database`。

> 密钥由客户端在本机生成并用于本地库。本仓库只处理"已经有密钥"的情况，不提供密钥提取工具。

## 3. WAL：最容易踩的坑

客户端用 WAL 模式写库，新消息往往还在 `nt_msg.db-wal` 里。

**关键**：如果你把数据库剥头后另存成 `foo.stripped.db`，那么 WAL 也必须复制成 **`foo.stripped.db-wal`**（同名 + `-wal`），否则 SQLite 打开时找不到配套 WAL，只能读到上次 checkpoint 的内容 —— 表现是"客户端里明明有新消息，程序读到的是几十分钟前的"。

```bash
cp nt_msg.db        work.db
cp nt_msg.db-wal    work.db-wal 2>/dev/null
cp nt_msg.db-shm    work.db-shm 2>/dev/null
dd if=work.db of=stripped.db bs=1024 skip=1
cp work.db-wal      stripped.db-wal 2>/dev/null   # ← 名字必须跟 stripped.db 配套
cp work.db-shm      stripped.db-shm 2>/dev/null
```

只想读最新一帧、不想处理 WAL 时，也可以直接读客户端当前打开的那个库文件（不剥头、不改名），但这会跟客户端争锁，不推荐。

## 4. 表结构（实测版本）

### 私聊 `c2c_msg_table`

| 字段 | 含义 |
|---|---|
| `[40001]` | 消息 id（平台唯一，去重键用它） |
| `[40020]` | 发送者 uid |
| `[40021]` | 对方 uid（会话 id 用它） |
| `[40030]` | 对方 QQ 号 |
| `[40033]` | 发送者 QQ 号 |
| `[40050]` | 时间戳（秒） |
| `[40800]` | 内容 blob（protobuf，里面嵌着 UTF-8 文本） |

**方向判定**：`[40033] == 自己的 QQ 号` → 出；否则 → 入。
不要用别的字段猜方向，实测 `[40011]` 跟方向无关。

**会话 id**：私聊用 `[40021]`（对方 uid），不要用 `[40020]` —— 出站消息的 `[40020]` 是自己。

### 群聊 `group_msg_table`

| 字段 | 含义 |
|---|---|
| `[40001]` | 消息 id |
| `[40020]` | 发送者 uid |
| `[40021]` / `[40027]` | 群号 |
| `[40033]` | 发送者 QQ 号 |
| `[40050]` | 时间戳 |
| `[40090]` / `[40093]` | 群名片 / 昵称 |
| `[40800]` | 内容 blob |

### @ 判定 `group_at_me_msg`

这个表记录"@ 到本账号"的消息，`[40001]` 就是消息 id：

```sql
SELECT [40001] FROM group_at_me_msg;
```

拿到的 id 集合与 `group_msg_table` 的 `[40001]` 求交集，就是"@我"的消息。

**注意**：`@全体成员` 通常也会进这张表。所以还要再看文本：

```
文本里有 @全体成员/@所有人  → 算 "all"
否则 id 命中 at_me        → 算 "bot"
文本以 @ 开头             → 算 "other"
其余                      → 算 "none"
```

策略层据此决定要不要回（默认只回 `bot`）。

## 5. 内容 blob

`[40800]` 是 protobuf，文本以 UTF-8 形式嵌在里面。最稳的做法不是完整解 protobuf，而是：

1. 先按 protobuf 的 `field 2 (bytes)` 取消息体；
2. 在字节流里找合法的 UTF-8 连续段（中文 3 字节、emoji 4 字节）；
3. 过滤掉控制字符。

图片/文件消息的文本部分是文件名，可作为附件类型标记。

## 6. 每轮读多少

建议每轮读**最近 N 条**（实测 N=200 足够覆盖 3 分钟内的群聊高峰）。N 太小时，两次轮询之间新增消息超过 N 就会丢。

如果想更稳，可以按"上一条的时间戳"做增量查询（`WHERE [40050] > lastTs`），但要注意保留几条重叠消息给差分算法对齐。

## 7. 读取成本

实测（单机、机械盘、200 条）：

| 操作 | 耗时 |
|---|---|
| 复制库 + WAL | 20–40 ms |
| 剥头 | 10 ms |
| SQLCipher 打开 + 查询 200 条 | 150–250 ms |
| **合计** | **约 200–300 ms / 轮** |

3 秒一轮完全无压力。
