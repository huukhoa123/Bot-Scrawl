from __future__ import annotations

import logging
from typing import Any

import httpx

from src.db import JobDB
from src.notifier import send_telegram_html

logger = logging.getLogger(__name__)

VALID_BUTTON_STATUSES = {"applied", "ignored", "saved"}

BOT_COMMANDS = [
    {"command": "search", "description": "Thêm từ khóa tìm việc (vd: /search python | HCM)"},
    {"command": "list", "description": "Xem danh sách từ khóa & trạng thái bot"},
    {"command": "remove", "description": "Xóa từ khóa theo ID (vd: /remove 1)"},
    {"command": "run", "description": "Quét việc làm mới ngay lập tức"},
    {"command": "pause", "description": "Tạm dừng gửi tin"},
    {"command": "resume", "description": "Bật lại bot"},
]


def register_bot_commands(bot_token: str) -> bool:
    """Register slash-command autocomplete menu in Telegram via setMyCommands."""
    if not bot_token:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/setMyCommands",
            json={"commands": BOT_COMMANDS},
            timeout=10.0,
        )
        return resp.status_code == 200 and bool(resp.json().get("ok"))
    except Exception as exc:
        logger.warning("Failed to register bot commands: %s", exc)
        return False


def process_command_text(text: str, db: JobDB) -> str | None:
    """Process a Telegram command string and return reply text (or None)."""
    raw = text.strip()
    if not raw.startswith("/"):
        return None

    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/search", "/add"):
        if not arg:
            rows = db.list_searches()
            current = "\n".join(f"#{sid}: <b>{kw}</b> ({loc or 'Mọi nơi'})" for sid, kw, loc in rows)
            hint = "Cú pháp: <code>/search &lt;keyword&gt; | &lt;location&gt;</code> (location tùy chọn)"
            return f"{hint}\n\n<b>Đang theo dõi:</b>\n{current}" if current else hint
        if "|" in arg:
            kw, loc = [p.strip() for p in arg.split("|", 1)]
        else:
            kw = arg.strip()
            loc = ""
        sid = db.add_search(kw, loc)
        return f"✅ Đã thêm tìm kiếm #{sid}: <b>{kw}</b> ({loc or 'Mọi nơi'})"

    if cmd == "/list":
        rows = db.list_searches()
        if not rows:
            return "Danh sách tìm kiếm đang trống."
        lines = [f"#{sid}: <b>{kw}</b> ({loc or 'Mọi nơi'})" for sid, kw, loc in rows]
        paused = db.get_state("paused", "0") == "1"
        status_line = "\n⏸ Trạng thái: <b>Đang tạm dừng</b>" if paused else "\n▶️ Trạng thái: <b>Đang hoạt động</b>"
        return "<b>Danh sách tìm kiếm:</b>\n" + "\n".join(lines) + status_line

    if cmd == "/remove":
        if not arg.isdigit():
            return "Cú pháp: <code>/remove &lt;id&gt;</code>"
        ok = db.remove_search(int(arg))
        return f"🗑 Đã xóa tìm kiếm #{arg}" if ok else f"Không tìm thấy tìm kiếm #{arg}"

    if cmd == "/pause":
        db.set_state("paused", "1")
        return "⏸ Đã tạm dừng gửi tin. Gõ /resume để tiếp tục."

    if cmd == "/resume":
        db.set_state("paused", "0")
        return "▶️ Đã bật lại bot."

    if cmd == "/run":
        return "🚀 Đang bắt đầu quét việc làm mới..."

    return None


def process_callback_data(data: str, db: JobDB) -> str | None:
    """Handle inline button callback_data: st:<status>:<source>:<job_id_prefix>."""
    if not data.startswith("st:"):
        return None
    parts = data.split(":", 3)
    if len(parts) != 4:
        return None
    _, status, source, jid_prefix = parts
    if status not in VALID_BUTTON_STATUSES:
        return None
    updated = db.mark_status_by_prefix(source, jid_prefix, status)
    if not updated:
        return "Không tìm thấy job trong DB."
    labels = {"applied": "✅ Đã đánh dấu Ứng tuyển", "ignored": "🚫 Đã bỏ qua", "saved": "⭐ Đã lưu"}
    return labels[status]


def sync_telegram_updates(
    bot_token: str,
    chat_id: str,
    db: JobDB,
    dry_run: bool = False,
    poll_timeout: int = 0,
) -> tuple[int, bool]:
    """Pull pending Telegram updates and apply commands/buttons.

    Returns (processed_count, should_trigger_crawl).
    """
    if dry_run or not bot_token or not chat_id:
        return 0, False

    last_id = int(db.get_state("last_update_id", "0") or "0")
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params: dict[str, Any] = {"timeout": poll_timeout}
    if last_id > 0:
        params["offset"] = last_id + 1

    processed = 0
    trigger_crawl = False
    try:
        with httpx.Client(timeout=float(poll_timeout) + 15.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("getUpdates failed with %d", resp.status_code)
                return 0, False
            updates = resp.json().get("result") or []

            max_uid = last_id
            for upd in updates:
                uid = int(upd.get("update_id", 0))
                if uid > max_uid:
                    max_uid = uid

                msg = upd.get("message")
                if isinstance(msg, dict):
                    sender_chat = str(msg.get("chat", {}).get("id", ""))
                    if sender_chat == str(chat_id) and msg.get("text"):
                        text_raw = str(msg["text"]).strip()
                        reply = process_command_text(text_raw, db)
                        if reply:
                            send_telegram_html(bot_token, chat_id, reply, client=client)
                            processed += 1
                            cmd = text_raw.split(maxsplit=1)[0].lower().split("@")[0]
                            if cmd in ("/search", "/add", "/run") and (cmd == "/run" or len(text_raw.split(maxsplit=1)) > 1):
                                trigger_crawl = True

                cb = upd.get("callback_query")
                if isinstance(cb, dict):
                    cb_chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                    if cb_chat == str(chat_id) and cb.get("data"):
                        ack = process_callback_data(str(cb["data"]), db)
                        if ack:
                            client.post(
                                f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                                json={"callback_query_id": cb["id"], "text": ack},
                            )
                            processed += 1

            if max_uid > last_id:
                db.set_state("last_update_id", str(max_uid))
    except Exception as exc:
        logger.warning("Failed to sync Telegram updates: %s", exc)

    return processed, trigger_crawl
