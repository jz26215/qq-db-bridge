/**
 * qq-db-bridge 的模块契约。
 * 实现其中 Reader / Sender，就可以接任意 Agent 框架。
 */

export type ChatType = 'private' | 'group';
export type MentionKind = 'bot' | 'all' | 'other' | 'none';

export interface Target {
  targetId: string;          // 你的内部会话键：group:<群号> / private:<uin>
  chatType: ChatType;
  displayName: string;
  dbChatKey?: string;        // 库里对应的会话键（群号 / 对方 uid）
}

export interface ChatEvent {
  targetId: string;
  chatType: ChatType;
  chatName: string;

  platformMessageId: string | null;   // 去重键的一部分；空则退化为内容键
  senderId: string;
  senderName: string;
  text: string;
  kind: 'text' | 'image' | 'other';
  self: boolean;

  ts: number;                         // 秒级
  atMe: boolean;                      // 命中 group_at_me_msg
  mention: MentionKind;               // 语义化后的 @ 类型
  isTopic?: boolean;                  // 情感 / 热度判定结果
  context?: Array<{ sender: string; text: string }>;
}

/** 轮询读库；实现必须只读，不要写客户端库。 */
export interface Reader {
  listTargets(): Promise<Target[]>;
  readRecent(target: Target, limit: number): Promise<ChatEvent[]>;
}

export type SendState = 'UI_VERIFIED' | 'ABORTED' | 'HANDOFF_UNKNOWN';

export interface SendOk {
  ok: true;
  verified: true;
  state: 'UI_VERIFIED';
  attemptId: string;
  sendMs?: number;
}

export interface SendFail {
  ok: false;
  reason: string;                     // moderation-blocked / db-verify-failed / paste-failed ...
  state: Exclude<SendState, 'UI_VERIFIED'>;
}

export type SendResult = SendOk | SendFail;

/** 发送；实现内部必须做 UI 确认 + 数据库回读双校验。 */
export interface Sender {
  send(target: Target, text: string): Promise<SendResult>;
}

/** 策略层：你的领域逻辑（回不回、回什么、审核、限流）。 */
export interface Policy {
  /** 输入：一批新消息；输出：要发送的回复（可空）。 */
  decide(events: ChatEvent[]): Promise<Array<{ target: Target; text: string; meta?: Record<string, unknown> }>>;
}

/** 入站/出站的内容闸。 */
export interface Moderator {
  /** 返回 ok=false 表示拒发；text 可用于打码后的替换。 */
  check(text: string, direction: 'inbound' | 'outbound'): Promise<
    | { ok: true; text: string }
    | { ok: false; reason: string }
  >;
}

/** 差分器：把一轮窗口和上一轮快照对齐，返回新增。 */
export interface Differ {
  diff(prevKeys: string[], windowEvents: ChatEvent[]): {
    ambiguous: boolean;
    partial: boolean;
    fresh: ChatEvent[];
  };
}
