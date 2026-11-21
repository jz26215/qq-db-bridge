# qq-db-bridge

**A different way to bridge QQ into an agent: don't touch the protocol — read the local message store, and let the client send.**

[中文说明](README.zh-CN.md) · English

Most QQ bots go through a protocol library (NapCat / Lagrange / OneBot). This repo takes another route:

```
read the local NTQQ message store (read-only, diff polling)
        ↓
map new rows into a unified ChatEvent and hand them to your agent / business layer
        ↓
when a reply is needed, drive a real QQ client to send it
```

It is **not** a deployable bot. It is an architecture plus three implementation notes you can embed into your own agent framework (DeepSeek Harness, a custom harness, or plain scripts).

> This repository intentionally contains **no key-extraction tooling** and no personal data.
> It documents how to read and send *once you already have* a local store and its key.

---

## Why this approach

| | Protocol route (NapCat etc.) | This approach (store read + client send) |
|---|---|---|
| Protocol reverse-engineering / login | Required | **Not required** — the client logs in itself |
| Disconnects, risk control | Depends on the implementation | Follows the official client |
| Message completeness | Push-only | The local store *is* the history — replayable, backfillable |
| Latency | Real-time push (better) | Polling interval (3 s measured, ~200 ms per round) |
| Group @ detection | Protocol event fields | `group_at_me_msg` table (precise) |
| Sending | Protocol send | Driving the client UI (**the fragile part**) |

**Good fit:** single machine, personal agent, you want an IM channel without protocol reverse-engineering, and a few seconds of latency is fine.

**Bad fit:** multi-account, high concurrency, millisecond responses, talking to many strangers.

---

## Architecture

```
┌────────────────────────────── your machine ──────────────────────────┐
│                                                                       │
│  ┌─────────────┐   copy + strip 1024B + SQLCipher   ┌──────────────┐  │
│  │  QQ client  │ ─────────────────────────────────→ │   Reader     │  │
│  │ nt_msg.db   │                                    │  (read-only) │  │
│  └─────────────┘                                    └──────┬───────┘  │
│         ▲                                                  │          │
│         │ writes                                      ChatEvent[]     │
│         │                                                  ▼          │
│  ┌─────────────┐                                    ┌──────────────┐   │
│  │  Sender     │ ←──────────── reply text ────────── │  Agent layer  │   │
│  │ (drives UI) │                                    │ policy/model  │   │
│  └─────────────┘                                    └──────────────┘   │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

Module contracts live in [`docs/04-architecture.md`](docs/04-architecture.md); the TypeScript interfaces are in [`src/types.ts`](src/types.ts).

---

## The three core pieces

| Doc | What it covers |
|---|---|
| [01-ntqq-db-decrypt.md](docs/01-ntqq-db-decrypt.md) | the 1024-byte private header, SQLCipher pragma order, table/column mapping, and the **WAL must be renamed to match the stripped DB** trap |
| [02-diff-polling.md](docs/02-diff-polling.md) | sequence diffing (LCS), dedupe key design, snapshot window, failing closed on ambiguity |
| [03-send-via-linux-qq.md](docs/03-send-via-linux-qq.md) | driving the client under Xvfb, CJK input must go through the clipboard, **UI acknowledgment + database read-back double check** |

Minimal runnable skeletons are in [`examples/`](examples).

---

## Quick start (Linux)

```bash
# 1) Bring your own store and key (no key extractor here)
export QQ_DB=/path/to/nt_msg.db
export QQ_DB_KEY='<16-byte key>'        # provided by your environment, never commit it
export QQ_SELF_UIN='<your uin>'         # used to tell inbound from outbound

# 2) Read the last 20 messages
python3 examples/read_messages.py --limit 20

# 3) Diff polling (every 3 s, print only new messages)
python3 examples/diff_poll.py --interval 3
```

Sending (needs Xvfb and a logged-in Linux QQ):

```bash
bash examples/send_via_linuxqq.sh "hello" "chat name"
```

---

## Design principles (lessons paid for in production)

1. **Sending needs two checks.** "The input box got cleared" is not proof; read back the client's own database and confirm the message actually landed.
2. **Fail-closed needs self-healing.** One unknown-outcome send blocks everything after it. Reconcile unresolved attempts against the database, or a single false negative silences the bot forever.
3. **The dedupe key must include the platform message id.** A sender+text key silently merges two genuinely identical consecutive messages.
4. **`@all` is not `@me`.** Both land in `group_at_me_msg`; you must inspect the text to tell them apart.
5. **CJK input must be pasted.** `xdotool type` turns non-ASCII into `?`; use the PRIMARY selection and a middle click.
6. **WAL files must match the stripped DB name.** Otherwise you read a point-in-time snapshot from tens of minutes ago.

---

## Risks and limits (read before use)

- Automating a client typically **violates the service's terms of use** and can get the account banned.
- This repo contains **no key-extraction tooling** and does not encourage circumventing client protection.
- Client upgrades may change table schemas or UI layout; the Sender is the first thing to break.
- Security software may block synthetic input; whitelist deliberately, not blindly.

See [`docs/05-risks-and-limits.md`](docs/05-risks-and-limits.md).

---

## Roadmap

- [ ] Timestamp-incremental reads (instead of a fixed window)
- [ ] Local small model for topic detection (instead of regex)
- [ ] Multi-account / multi-instance routing
- [ ] Attachment (image/file) capture and classification
- [ ] Web admin panel

---

## License

Code under MIT; docs under CC BY 4.0. For personal learning and self-hosting only. The authors are not responsible for account bans or data loss.
