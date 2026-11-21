#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""差分轮询示例：每 N 秒读一次库，打印新增消息。

依赖 read_messages.py（同目录）。

用法：
    python3 diff_poll.py --interval 3 --limit 200
"""
import argparse
import json
import os
import time

import read_messages as rm

SNAPSHOT_PATH = os.environ.get("QQ_SNAPSHOT", "snapshot.json")


def key_of(ev: dict) -> str:
    """对齐键：平台消息 id 唯一时用它，否则退化为内容键。"""
    return json.dumps(
        [
            2,
            ev.get("kind", "text"),
            "me" if ev.get("self") else "them",
            ev.get("senderName") or "",
            ev.get("text") or "",
            ev.get("msgId") or "",
        ],
        ensure_ascii=False,
    )


def lcs_pairs(a: list, b: list):
    """最长公共子序列对齐，返回匹配下标对。"""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    width = m + 1
    dp = [[0] * width for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def load_snapshot() -> list:
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def save_snapshot(keys: list) -> None:
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(keys[-200:], fh, ensure_ascii=False)


def diff(prev_keys: list, events: list) -> dict:
    cur_keys = [key_of(ev) for ev in events]
    if not prev_keys:
        return {"ambiguous": False, "partial": False, "fresh": events}
    pairs = lcs_pairs(prev_keys, cur_keys)
    if not pairs:
        return {"ambiguous": True, "partial": False, "fresh": []}
    last_prev, last_cur = pairs[-1]
    return {
        "ambiguous": False,
        "partial": last_prev != len(prev_keys) - 1,
        "fresh": events[last_cur + 1:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    db_path = os.environ.get("QQ_DB")
    key = os.environ.get("QQ_DB_KEY")
    self_uin = os.environ.get("QQ_SELF_UIN", "")
    if not db_path or not key:
        print("请设置 QQ_DB / QQ_DB_KEY")
        return 2

    stripped = rm.prepare_db(db_path)
    conn = rm.open_db(stripped, key)
    at_me = rm.load_at_me_ids(conn)

    print(f"polling {db_path} every {args.interval}s ...")
    while True:
        events = []
        for table, kind in (("c2c_msg_table", "c2c"), ("group_msg_table", "group")):
            for row in rm.rows(conn, table, args.limit):
                events.append(rm.to_event(row, kind, self_uin, at_me))
        events.sort(key=lambda e: e["ts"])

        result = diff(load_snapshot(), events)
        if result["ambiguous"]:
            print("[ambiguous] 窗口和快照对不上，本轮跳过，不推进快照")
        else:
            for ev in result["fresh"]:
                if not ev["self"]:
                    print(json.dumps(ev, ensure_ascii=False))
            save_snapshot([key_of(ev) for ev in events])

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
