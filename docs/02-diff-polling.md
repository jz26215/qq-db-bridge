# 二、差分轮询：怎么判断"哪条是新消息"

> 目标：每轮读到的是一段窗口（比如最近 200 条），要和上一轮对齐，找出新增的那几条，且**不能重复、不能丢**。

## 1. 为什么不能只用"最后一条 id"

看起来最简单的做法是记一个 `lastMsgId`，下一轮取 id 更大的。有三个问题：

1. 同一毫秒内可能有多条消息，id 不保证单调；
2. 客户端重启、补拉历史会打乱顺序；
3. 一条消息被撤回/删除后，"最后一条"会后退，导致重发。

## 2. 用序列差分（LCS）

把每轮的窗口当成一个**序列**，和上一轮快照做最长公共子序列对齐：

```
上一轮快照: [A, B, C, D, E]
这一轮窗口:       [C, D, E, F, G]
                    ↑ 对齐点
新增: [F, G]
```

- 能对齐 → 对齐点之后的就是新增；
- 完全对不上（`pairs.length == 0`）→ 标 `ambiguous`，**这一轮不推快照、不投递**，等下轮再看；
- 上一轮快照的尾部不在当前窗口里（窗口滚出）→ 标 `partial`，仍然可以投递，但要标记"序列不完整"。

关键点：**对齐键必须唯一**。见下一节。

## 3. 去重键（对齐键）设计

最直觉的键是 `(发送者, 类型, 文本)`，但它会合并真实存在的重复消息：

```
群友甲: @机器人
群友甲: @机器人     ← 10 秒后连发两条一模一样的
```

用内容键时，这两条会被 LCS 认为是同一条，第二条永远丢。

**正确做法**：把平台消息 id 放进键里：

```python
def key_of(m):
    platform_id = m["msg_id"] if m["msg_id"] else ""
    return json.dumps([VERSION, m["kind"], "me" if m["self"] else "them",
                       m["sender"] or "", m["text"] or "", platform_id])
```

- 有平台 id（库读通道）→ 每条都唯一，连发两条也能各自对齐；
- 没有平台 id（UI 读通道）→ 退化成内容键，仍然可用，但会合并相同文本。

## 4. 快照窗口与容量

快照只需要存**当前窗口的对齐键序列**，不需要存全量：

```python
snapshot = current_window_keys[-200:]   # 与读取窗口同长
```

如果两轮之间新增消息超过窗口长度，超出的部分会被判 `partial` 并丢掉——这是设计上的已知边界。实测群聊高峰约 15 条/分钟，200 的窗口有十几倍余量；真遇到刷屏机器人，要么加大窗口，要么改成时间戳增量查询。

## 5. 每个数据源单独一份快照

如果你同时有两条读通道（比如"读库"和"读界面"），它们的对齐键格式不同（一个有平台 id，一个没有），**必须分开存快照**，否则永远对不上、每轮都判歧义：

```python
snapshot_key = f"target:{target_id}:{source}"   # source = 'db' | 'ui'
```

## 6. 歧义处理：fail-closed

```
完全没有对齐点 → ambiguous
  ├── 记录本次歧义（保留现场：窗口长度、快照长度）
  ├── 不投递任何消息（宁可这条晚一点，也不要发错）
  └── 不更新快照（下轮重新尝试对齐）
```

## 7. 一轮完整的伪代码

```python
def poll_once(target):
    window = read_recent(target, limit=200)         # 库读，约 200ms
    prev = store.snapshot(target)                   # 上一轮的对齐键
    diff = lcs_diff(prev, [key_of(m) for m in window])

    if diff.ambiguous:
        store.record_ambiguity(target, window, diff)
        return {"skipped": "ambiguous"}

    fresh = window[diff.last_matched_index + 1:]    # 新增消息
    if fresh:
        records = [to_record(m) for m in fresh if not m.self]
        store.append_inbox(records)                 # 先写落盘
        on_new_messages(target, records)            # 再交给上层
    store.save_snapshot(target, [key_of(m) for m in window[-200:]])
    return {"fresh": len(fresh)}
```

顺序很重要：**先写 inbox 再更新快照**。最坏情况是下一轮重复记一次（幂等，可去重），而不是丢消息。

## 8. 自测方法

拿一段真实消息记录（`{msg_id, ts, sender, text, self}` 的 JSONL），把它切成 N 条一批喂给轮询函数：

| 每轮喂入 | 期望 |
|---|---|
| 5 条 | 全部识别为新增 |
| 60 条 | 全部识别为新增 |
| 100 条 | 全部识别为新增 |
| 超过窗口长度（如 250） | 部分丢失，且能报出 `partial` |

这是检验"去重键是否有唯一性"最直接的办法——如果批量 5 条时少了消息，说明键把重复内容合并了。
