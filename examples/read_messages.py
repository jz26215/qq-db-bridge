#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小可运行示例：只读地读 NTQQ 本地消息库。

依赖：
    pip install sqlcipher3-binary

用法：
    export QQ_DB=/path/to/nt_msg.db
    export QQ_DB_KEY='<16 字节密钥>'          # 由你的环境提供
    export QQ_SELF_UIN='<自己的 QQ 号>'        # 用于判断消息方向
    python3 read_messages.py --limit 20
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HEADER_BYTES = 1024

# 两张表的列顺序不同，分开写清楚，避免 SELECT 不存在的列
C2C_COLUMNS = "[40001],[40020],[40021],[40030],[40033],[40050],[40800]"
GROUP_COLUMNS = "[40001],[40020],[40021],[40027],[40033],[40050],[40090],[40800]"


def strip_header(src_path: str, dst_path: str) -> None:
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        src.seek(HEADER_BYTES)
        shutil.copyfileobj(src, dst)


def prepare_db(db_path: str) -> str:
    """复制库 + WAL + SHM，剥掉 1024 字节私有头。WAL 必须跟新库同名。"""
    workdir = tempfile.mkdtemp(prefix="qqdb-")
    tmp_db = os.path.join(workdir, "work.db")
    stripped = os.path.join(workdir, "stripped.db")

    shutil.copyfile(db_path, tmp_db)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            shutil.copyfile(db_path + suffix, tmp_db + suffix)

    strip_header(tmp_db, stripped)
    # 关键：剥头后的库名变了，WAL/SHM 也要改成配对的同名，否则读不到最新帧
    for suffix in ("-wal", "-shm"):
        if os.path.exists(tmp_db + suffix):
            shutil.copyfile(tmp_db + suffix, stripped + suffix)
    return stripped


def open_db(path: str, key: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    # 顺序不能变：page_size 在 key 之前，KDF 参数在 key 之后
    conn.execute("PRAGMA cipher_page_size = 4096;")
    conn.execute(f"PRAGMA key = '{key}';")
    conn.execute("PRAGMA kdf_iter = 4000;")
    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
    return conn


def load_at_me_ids(conn) -> set:
    try:
        return {str(row[0]) for row in conn.execute("SELECT [40001] FROM group_at_me_msg;")}
    except Exception:
        return set()


def extract_text(blob: bytes) -> str:
    """从 protobuf 内容里挑出 UTF-8 文本段。够用即可，不必完整解析。"""
    if not blob:
        return ""
    text = blob.decode("utf-8", "ignore")
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\t").strip()


def rows(conn, kind: str, limit: int):
    """kind: 'c2c' | 'group'；返回最近 limit 条（时间倒序）。"""
    if kind == "c2c":
        sql = f"SELECT {C2C_COLUMNS} FROM c2c_msg_table ORDER BY [40050] DESC LIMIT ?"
    else:
        sql = f"SELECT {GROUP_COLUMNS} FROM group_msg_table ORDER BY [40050] DESC LIMIT ?"
    return list(conn.execute(sql, (limit,)))


def to_event(row, kind: str, self_uin: str, at_me_ids: set) -> dict:
    if kind == "c2c":
        msg_id, sender_uid, peer_uid, peer_uin, sender_uin, ts, blob = row
        group_code, card = "", ""
        chat_id = str(peer_uid or "")
    else:
        msg_id, sender_uid, group_code, group_code2, sender_uin, ts, card, blob = row
        chat_id = str(group_code or group_code2 or "")

    out = str(sender_uin) == str(self_uin)
    text = extract_text(blob)
    mention = "none"
    if "@全体成员" in text or "@所有人" in text:
        mention = "all"
    elif str(msg_id) in at_me_ids:
        mention = "bot"
    elif text.lstrip().startswith("@"):
        mention = "other"
    return {
        "kind": kind,
        "chatId": chat_id,
        "msgId": str(msg_id),
        "senderUin": str(sender_uin),
        "senderName": str(card or ""),
        "text": text,
        "ts": int(ts or 0),
        "self": out,
        "atMe": mention == "bot",
        "mention": mention,
    }


def read_window(conn, self_uin: str, at_me_ids: set, limit: int):
    events = []
    for kind in ("c2c", "group"):
        for row in rows(conn, kind, limit):
            events.append(to_event(row, kind, self_uin, at_me_ids))
    events.sort(key=lambda e: e["ts"])
    return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    db_path = os.environ.get("QQ_DB")
    key = os.environ.get("QQ_DB_KEY")
    self_uin = os.environ.get("QQ_SELF_UIN", "")
    if not db_path or not key:
        print("请设置 QQ_DB 和 QQ_DB_KEY 环境变量", file=sys.stderr)
        return 2

    stripped = prepare_db(db_path)
    conn = open_db(stripped, key)
    at_me = load_at_me_ids(conn)

    for ev in read_window(conn, self_uin, at_me, args.limit):
        print(json.dumps(ev, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
