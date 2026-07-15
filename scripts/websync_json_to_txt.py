#!/usr/bin/env python3
"""
Convert WhatsApp Web extraction JSON into the staging layout that
websync_whatsapp_import.py expects.

Input JSON (one file per chat, produced by the browser extraction snippet):
    {"chat": "Victor AG Dgo Rodeo", "msgs": [{"ts": 1784088840000, "sender": "...", "text": "..."}]}

Output:
    <staging_dir>/<Chat Name>/_chat.txt
    with lines: [14/07/26, 22:16] Sender: text

Timestamps are epoch ms interpreted in local time — same clock the browser
used to parse them, so round-tripping is exact at minute precision.

Usage: python scripts/websync_json_to_txt.py <staging_dir> <chat1.json> [chat2.json ...]
"""
import json
import os
import re
import sys
from datetime import datetime


def sanitize_dirname(name: str) -> str:
    """Chat names become directory names — strip path separators and control chars."""
    name = re.sub(r"[/\\:\x00-\x1f]", "-", name).strip().strip(".")
    return name[:80] or "unknown-chat"


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/websync_json_to_txt.py <staging_dir> <chat1.json> [...]")
        sys.exit(1)

    staging = sys.argv[1]
    os.makedirs(staging, exist_ok=True)

    for json_path in sys.argv[2:]:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, str):  # double-encoded (tool saved the JSON string as a string)
            data = json.loads(data)

        chats = data if isinstance(data, list) else [data]
        for entry in chats:
            if "error" in entry:
                print(f"SKIP {entry.get('chat', json_path)}: extraction error: {entry['error']}")
                continue

            chat = entry.get("chat", "unknown-chat")
            msgs = entry.get("msgs", [])
            if not msgs:
                print(f"SKIP {chat}: no messages")
                continue

            lines = []
            for m in msgs:
                dt = datetime.fromtimestamp(m["ts"] / 1000)
                # 24h format, no seconds — parser fmt "%d/%m/%y %H:%M"
                lines.append(f"[{dt.strftime('%d/%m/%y, %H:%M')}] {m['sender']}: {m['text']}")

            chat_dir = os.path.join(staging, sanitize_dirname(chat))
            os.makedirs(chat_dir, exist_ok=True)
            out_path = os.path.join(chat_dir, "_chat.txt")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"OK {chat}: {len(msgs)} messages -> {out_path}")


if __name__ == "__main__":
    main()
