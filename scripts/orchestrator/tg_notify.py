#!/usr/bin/env python3
"""Send a stage-completion notification to Telegram for trader tasks.

Usage:
  python3 scripts/orchestrator/tg_notify.py --task-type closing --date 2026-03-24 \
      --stage write --status done --note "draft 12k chars"
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OPENCLAW_CONFIG = Path(__file__).resolve().parents[3] / "openclaw.json"
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TASK_TYPE_LABELS = {
    "premarket": "盘前分析",
    "closing": "收盘复盘",
    "intraday": "盘中异动",
    "weekly": "周报",
    "philosophy": "投资框架",
}

STAGE_LABELS = {
    "collect": "数据采集",
    "normalize": "数据规整",
    "discuss": "多角色讨论",
    "write": "报告撰写",
    "review": "质量审核",
    "revise": "修订回修",
    "deliver": "交付打包",
}

STATUS_EMOJI = {
    "done": "\u2705",
    "skipped": "\u23ed\ufe0f",
    "failed": "\u274c",
    "running": "\U0001f504",
}


def _get_bot_token() -> str:
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
        return cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
    except Exception:
        return ""


def _debug_payload(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def send_telegram(text: str) -> bool:
    bot_token = _get_bot_token()
    if not bot_token:
        _debug_payload({"notification": True, "chat_id": CHAT_ID, "message": text})
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
        return True
    except Exception:
        _debug_payload({"notification": True, "chat_id": CHAT_ID, "message": text})
        return False


def send_telegram_document(file_path: str, caption: str = "") -> bool:
    bot_token = _get_bot_token()
    path = Path(file_path)
    if not bot_token or not path.exists():
        _debug_payload({
            "notification": True,
            "chat_id": CHAT_ID,
            "document": str(path),
            "caption": caption,
            "exists": path.exists(),
        })
        return False

    boundary = "----OpenClawTelegramBoundary7MA4YWxkTrZu0gW"
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    caption_bytes = caption.encode("utf-8")
    file_bytes = path.read_bytes()

    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        f"{CHAT_ID}\r\n".encode(),
    ]

    if caption:
        parts.extend([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="caption"\r\n\r\n',
            caption_bytes,
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="parse_mode"\r\n\r\n',
            b"HTML\r\n",
        ])

    parts.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])

    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception:
        _debug_payload({
            "notification": True,
            "chat_id": CHAT_ID,
            "document": str(path),
            "caption": caption,
        })
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Send trader pipeline stage notification to Telegram")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", default="done", choices=["done", "skipped", "failed", "running"])
    parser.add_argument("--note", default="")
    parser.add_argument("--total-stages", type=int, default=0)
    parser.add_argument("--completed-stages", type=int, default=0)
    parser.add_argument("--document", default="")
    parser.add_argument("--caption", default="")
    args = parser.parse_args()

    emoji = STATUS_EMOJI.get(args.status, "\u2139\ufe0f")
    task_label = TASK_TYPE_LABELS.get(args.task_type, args.task_type)
    stage_label = STAGE_LABELS.get(args.stage, args.stage)
    note_line = f"\n\u5907\u6ce8\uff1a{args.note}" if args.note else ""

    progress = ""
    if args.total_stages > 0 and args.completed_stages > 0:
        progress = f" ({args.completed_stages}/{args.total_stages})"

    text = (
        f"{emoji} <b>{task_label}</b> \u00b7 {stage_label} {args.status}{progress}\n"
        f"\U0001f4c5 {args.date}"
        f"{note_line}"
    )

    if args.document:
        caption = args.caption or text
        send_telegram_document(args.document, caption)
        return

    send_telegram(text)


if __name__ == "__main__":
    main()
