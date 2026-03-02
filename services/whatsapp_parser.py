"""
Parser for WhatsApp .txt chat exports.
Format: [DD/MM/YY, HH:MM:SS a.m./p.m.] Sender Name: Message
Also handles: "image omitted", "document omitted", etc.
"""
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# WhatsApp message line pattern (handles both 12h and 24h formats)
# Format 1 (with brackets):  [13/12/24, 9:38:48 a.m.] Sender: message
# Format 2 (without brackets): 4/1/24, 14:47 - Sender: message
MESSAGE_PATTERN = re.compile(
    r'^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+'
    r'(\d{1,2}:\d{2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.|AM|PM)?)\]?\s*'
    r'[-–]?\s*'
    r'([^:]+):\s+(.*)',
    re.MULTILINE
)

OMITTED_PATTERN = re.compile(
    r'(image|video|audio|sticker|GIF|document|contact card)\s+omitted',
    re.IGNORECASE
)


def parse_whatsapp_chat(text: str) -> List[Dict]:
    """
    Parse a WhatsApp chat export into structured messages.
    Returns: [{"timestamp": datetime, "sender": str, "message": str, "has_attachment": bool}]
    """
    # Strip Unicode control characters (LTR marks, zero-width spaces, etc.)
    text = re.sub(r'[\u200e\u200f\u200b\u200c\u200d\u2069\ufeff]', '', text)

    messages = []
    current_message = None

    for line in text.split('\n'):
        match = MESSAGE_PATTERN.match(line.strip())
        if match:
            if current_message:
                messages.append(current_message)

            date_str, time_str, sender, message = match.groups()

            timestamp = _parse_timestamp(date_str, time_str)
            has_attachment = bool(OMITTED_PATTERN.search(message))

            current_message = {
                "timestamp": timestamp,
                "sender": sender.strip(),
                "message": message.strip(),
                "has_attachment": has_attachment,
            }
        elif current_message:
            # Continuation of previous message
            current_message["message"] += "\n" + line.strip()

    if current_message:
        messages.append(current_message)

    return messages


def _parse_timestamp(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse various WhatsApp date/time formats."""
    formats = [
        "%d/%m/%y %I:%M:%S %p",
        "%d/%m/%y %I:%M %p",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
    ]
    # Normalize AM/PM markers
    time_str = time_str.replace('a.m.', 'AM').replace('p.m.', 'PM').strip()
    combined = f"{date_str} {time_str}"

    for fmt in formats:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    return None


def chunk_conversation(
    messages: List[Dict],
    time_window_minutes: int = 60,
    max_chunk_messages: int = 30,
) -> List[Dict]:
    """
    Chunk a parsed WhatsApp conversation into logical segments.
    Groups messages by time proximity.

    Returns: [{"text": str, "start_time": datetime, "end_time": datetime,
               "participants": [str], "message_count": int}]
    """
    if not messages:
        return []

    chunks = []
    current_chunk_messages = []
    chunk_start = messages[0].get("timestamp")

    for msg in messages:
        if current_chunk_messages:
            prev_ts = current_chunk_messages[-1].get("timestamp")
            curr_ts = msg.get("timestamp")

            time_gap = False
            if prev_ts and curr_ts:
                time_gap = (curr_ts - prev_ts) > timedelta(minutes=time_window_minutes)

            size_limit = len(current_chunk_messages) >= max_chunk_messages

            if time_gap or size_limit:
                chunks.append(_finalize_chunk(current_chunk_messages, chunk_start))
                current_chunk_messages = []
                chunk_start = msg.get("timestamp")

        current_chunk_messages.append(msg)

    if current_chunk_messages:
        chunks.append(_finalize_chunk(current_chunk_messages, chunk_start))

    return chunks


def _finalize_chunk(messages: List[Dict], start_time) -> Dict:
    """Create a chunk dict from a list of messages."""
    participants = list(set(m["sender"] for m in messages))
    end_time = messages[-1].get("timestamp")

    lines = []
    for m in messages:
        ts = m["timestamp"].strftime("%Y-%m-%d %H:%M") if m["timestamp"] else ""
        lines.append(f"[{ts}] {m['sender']}: {m['message']}")

    combined_text = "\n".join(lines)

    return {
        "text": combined_text,
        "start_time": start_time,
        "end_time": end_time,
        "participants": participants,
        "message_count": len(messages),
    }
