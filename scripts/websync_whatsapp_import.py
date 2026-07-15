#!/usr/bin/env python3
"""
Import WhatsApp Web-scraped chats into the RAG pipeline (text only).

Expects a staging directory laid out as:
    <staging>/<Chat Name>/_chat.txt

Each _chat.txt uses standard WhatsApp export format lines:
    [14/07/26, 22:16] Sender: message

The inner filename is deliberately `_chat.txt` so deduplication shares the
same universe as iOS phone-export zips (dedup matches on original_filename,
and message hashes are minute-precision — see whatsapp_parser._hash_message).
A message captured here will not re-import when the same period later arrives
via a phone zip export, and vice versa.

Media is NOT captured by web sync — messages with attachments appear as
"image omitted" / "<filename> · document omitted" placeholders so conversation
context stays intact. Phone zip exports remain the source of truth for files.

Usage: python scripts/websync_whatsapp_import.py <staging_dir>
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.weekly_whatsapp_import import process_single_file


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print("Usage: python scripts/websync_whatsapp_import.py <staging_dir>")
        print("  where <staging_dir> contains one <Chat Name>/_chat.txt per chat")
        sys.exit(1)

    staging = sys.argv[1]
    chat_dirs = sorted(
        d for d in os.listdir(staging)
        if os.path.isfile(os.path.join(staging, d, "_chat.txt"))
    )
    if not chat_dirs:
        print(f"No <Chat Name>/_chat.txt found under {staging}")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"  WhatsApp Web Sync Import")
    print(f"  Chats: {len(chat_dirs)}")
    for d in chat_dirs:
        print(f"    {d}")
    print(f"{'=' * 60}")

    t_total = time.time()
    for i, d in enumerate(chat_dirs, 1):
        process_single_file(
            os.path.join(staging, d, "_chat.txt"),
            i, len(chat_dirs),
            chat_name_override=d,
            sweep_pending=False,
        )

    elapsed = time.time() - t_total
    print(f"\n{'=' * 60}")
    print(f"  ALL DONE — {len(chat_dirs)} chat(s) in {int(elapsed // 60)}m{int(elapsed % 60)}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
