# 四、架构与扩展点

> 这个仓库的定位：**一套可以被继续开发的骨架**，不是成品机器人。
> 你只需要实现两个接口，就能把它接到任意 Agent / 业务层。

## 1. 三个模块，两个边界

```
┌──────────┐   ChatEvent[]   ┌─────────────┐   Reply   ┌──────────┐
│  Reader  │ ──────────────→ │   Policy    │ ────────→ │  Sender  │
│ (只读)    │                 │ (你的逻辑)   │           │ (驱动客户端)│
└──────────┘                 └─────────────┘           └──────────┘
     ↑                                                       │
     │ 轮询                                                   │ 回读校验
     └──────────────────── 客户端数据库 ←──────────────────────┘
```

- **Reader**：轮询库，产出 `ChatEvent`。与策略无关。
- **Policy**：你的领域逻辑——要不要回、回什么、审核、限流、多角色调度。仓库只给默认实现（只回 @我 + 重要话题）。
- **Sender**：把一个回复发出去，并**用数据库回读**确认。

## 2. ChatEvent（统一事件）

```ts
interface ChatEvent {
  // 会话
  targetId: string;        // 你的内部会话标识，如 group:<群号> / private:<uin>
  chatType: 'private' | 'group';
  chatName: string;        // 显示名，仅用于日志

  // 消息
  platformMessageId: string | null;  // 平台消息 id，去重键用它
  senderId: string;                  // 发送者 uid
  senderName: string;                // 昵称/群名片
  text: string;
  kind: 'text' | 'image' | 'other';
  self: boolean;                     // 是否自己发的

  // 时间与判定
  ts: number;                        // 秒级时间戳
  atMe: boolean;                     // 命中 @我 表
  mention: 'bot' | 'all' | 'other' | 'none';   // @ 语义（见 01 文档）
  isTopic?: boolean;                 // 话题判定（情感/热度），可选
  context?: Array<{ sender: string; text: string }>;  // 群聊上文，可选
}
```

## 3. 两个必实现接口

```ts
interface Reader {
  /** 列出要轮询的会话 */
  listTargets(): Promise<Target[]>;
  /** 读目标会话最近 N 条（按时间升序） */
  readRecent(target: Target, limit: number): Promise<ChatEvent[]>;
}

interface Sender {
  /** 发一条消息；实现内部必须做「UI 确认 + 数据库回读」 */
  send(target: Target, text: string): Promise<SendResult>;
}

type SendResult =
  | { ok: true; verified: true; attemptId: string }
  | { ok: false; reason: string; state: 'ABORTED' | 'HANDOFF_UNKNOWN' };
```

仓库里的 `examples/read_messages.py` 和 `examples/send_via_linuxqq.sh` 就是这两个接口的最小实现。

## 4. 主循环（30 行）

```ts
while (true) {
  for (const target of await reader.listTargets()) {
    const window = await reader.readRecent(target, 200);
    const diff = diffAgainstSnapshot(target, window);   // 见 02 文档

    if (diff.ambiguous) continue;                       // fail-closed

    const fresh = diff.fresh.filter((e) => !e.self);
    if (fresh.length === 0) { commitSnapshot(target, window); continue; }

    store.appendInbox(fresh);                           // 先落盘
    const replies = await policy.decide(fresh);         // 你的逻辑
    for (const reply of replies) {
      const result = await sender.send(target, reply.text);
      store.recordAttempt(reply, result);
    }
    commitSnapshot(target, window);                      // 后提交快照
  }
  await sleep(POLL_INTERVAL_MS);                         // 2000–5000ms
}
```

## 5. Policy 的可扩展点

默认策略（可直接替换）：

| 场景 | 默认行为 | 扩展方向 |
|---|---|---|
| 私聊 | 每条都回 | 白名单、免打扰时段 |
| 群聊 `@我` | 回 | 多角色路由（按话题分给不同 persona） |
| 群聊 `@全体` | 不回 | 配置项打开 |
| 群聊 `@别人` | 不回 | —— |
| 群聊无 @ 但有情感词 | 回（可配） | 换成本地小模型判定 |
| 群聊话题热度（N 条跟帖） | 回（可配） | 换成语义聚类 |

**重要**：`@全体` 在库里通常也算"@到我"，必须单独判语义，否则机器人会对全体通知乱插嘴。

## 6. 建议的工程结构

```
qq-db-bridge/
├── reader/        # 库读（本仓库 examples/read_messages.py 是雏形）
├── diff/          # 差分（examples/diff_poll.py）
├── sender/        # 客户端发送（examples/send_via_linuxqq.sh）
├── policy/        # 你的策略层（仓库只给默认实现）
├── store/         # inbox / 快照 / 事务状态（SQLite 或 JSONL 均可）
└── docs/
```

## 7. 想接到自己的 Agent 框架

最小接入步骤：

1. 用 `Reader` 拿到 `ChatEvent`；
2. 把 `ChatEvent` 转成你框架的"用户消息"（带上 `targetId` 作为会话键）；
3. 框架产出回复后，调 `Sender.send`；
4. **把 `SendResult` 写回你的框架**：`HANDOFF_UNKNOWN` 必须能触发人工核对，不能当成功。

## 8. 可以继续做的方向

- [ ] Windows 版客户端发送适配（当前只写了 Linux）
- [ ] 按时间戳增量读库（替代固定窗口，彻底解决大爆发丢消息）
- [ ] 本地小模型做 `isTopic` 判定（替代正则）
- [ ] 多账号 / 多实例路由
- [ ] 消息附件（图片/文件）本地落盘与内容识别
- [ ] Web 管理面板（会话、开关、发送队列）
