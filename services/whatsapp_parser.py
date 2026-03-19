"""
Parser for WhatsApp .txt chat exports.
Format: [DD/MM/YY, HH:MM:SS a.m./p.m.] Sender Name: Message
Also handles: "image omitted", "document omitted", etc.

Enhanced to extract media references with filenames and surrounding context.
"""
import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple


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

# Simple omitted patterns (no filename)
OMITTED_PATTERN = re.compile(
    r'(image|video|audio|sticker|GIF|document|contact card)\s+omitted',
    re.IGNORECASE
)

# Spanish multimedia omitted
MULTIMEDIA_OMITIDO = re.compile(r'<Multimedia omitido>', re.IGNORECASE)

# Document with filename: "filename.pdf · 2 pages · document omitted"
# Captures: filename, optional page count, media type
DOCUMENT_WITH_FILENAME = re.compile(
    r'^(.+?\.(?:pdf|docx?|xlsx?|xml|csv|pptx?|zip|rar))\s*'
    r'(?:[·•]\s*(\d+)\s*pages?\s*)?'
    r'[·•]?\s*document\s+omitted',
    re.IGNORECASE
)

# Image/video/audio with filename (less common but possible)
MEDIA_WITH_FILENAME = re.compile(
    r'^(.+?\.(?:jpe?g|png|gif|bmp|webp|mp4|mov|avi|mp3|ogg|opus|m4a|aac))\s*'
    r'[·•]?\s*(image|video|audio)\s+omitted',
    re.IGNORECASE
)

# Media type keywords for classification
MEDIA_TYPE_MAP = {
    'image': 'image',
    'video': 'video',
    'audio': 'audio',
    'sticker': 'sticker',
    'gif': 'image',
    'document': 'document',
    'contact card': 'contact',
}


def parse_whatsapp_chat(text: str) -> List[Dict]:
    """
    Parse a WhatsApp chat export into structured messages.
    Returns: [{
        "timestamp": datetime,
        "sender": str,
        "message": str,
        "has_attachment": bool,
        "attachment_info": {
            "type": "image"|"video"|"audio"|"document"|"sticker"|"contact",
            "filename": str | None,
            "page_count": int | None,
        } | None,
        "message_hash": str,  # For deduplication
    }]
    """
    # Strip Unicode control characters (LTR marks, zero-width spaces, etc.)
    text = re.sub(r'[\u200e\u200f\u200b\u200c\u200d\u2069\ufeff]', '', text)

    messages = []
    current_message = None

    for line in text.split('\n'):
        match = MESSAGE_PATTERN.match(line.strip())
        if match:
            if current_message:
                current_message["message_hash"] = _hash_message(current_message)
                messages.append(current_message)

            date_str, time_str, sender, message = match.groups()

            timestamp = _parse_timestamp(date_str, time_str)
            attachment_info = _extract_attachment_info(message.strip())
            has_attachment = attachment_info is not None

            current_message = {
                "timestamp": timestamp,
                "sender": sender.strip(),
                "message": message.strip(),
                "has_attachment": has_attachment,
                "attachment_info": attachment_info,
            }
        elif current_message:
            # Continuation of previous message
            current_message["message"] += "\n" + line.strip()

    if current_message:
        current_message["message_hash"] = _hash_message(current_message)
        messages.append(current_message)

    return messages


def _extract_attachment_info(message: str) -> Optional[Dict]:
    """Extract attachment metadata from a message."""
    # Check for document with filename first (most specific)
    doc_match = DOCUMENT_WITH_FILENAME.match(message)
    if doc_match:
        filename = doc_match.group(1).strip()
        page_count = int(doc_match.group(2)) if doc_match.group(2) else None
        return {
            "type": "document",
            "filename": filename,
            "page_count": page_count,
        }

    # Check for image/video/audio with filename
    media_match = MEDIA_WITH_FILENAME.match(message)
    if media_match:
        filename = media_match.group(1).strip()
        media_type = MEDIA_TYPE_MAP.get(media_match.group(2).lower(), 'document')
        return {
            "type": media_type,
            "filename": filename,
            "page_count": None,
        }

    # Check for Spanish <Multimedia omitido>
    if MULTIMEDIA_OMITIDO.search(message):
        return {
            "type": "unknown",
            "filename": None,
            "page_count": None,
        }

    # Check for simple English omitted pattern
    omitted_match = OMITTED_PATTERN.search(message)
    if omitted_match:
        media_type = MEDIA_TYPE_MAP.get(omitted_match.group(1).lower(), 'document')
        return {
            "type": media_type,
            "filename": None,
            "page_count": None,
        }

    return None


def _hash_message(msg: Dict) -> str:
    """Create a stable hash for deduplication. Uses timestamp + sender + message content."""
    ts_str = msg["timestamp"].isoformat() if msg.get("timestamp") else ""
    raw = f"{ts_str}|{msg['sender']}|{msg['message']}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def extract_media_with_context(
    messages: List[Dict],
    context_window: int = 3,
) -> List[Dict]:
    """
    Extract all media references from parsed messages with surrounding context.

    Returns: [{
        "index": int,              # Position in message list
        "timestamp": datetime,
        "sender": str,
        "attachment_info": dict,   # type, filename, page_count
        "context_before": [str],   # Up to N messages before
        "context_after": [str],    # Up to N messages after
        "message_hash": str,
    }]
    """
    media_refs = []

    for i, msg in enumerate(messages):
        if not msg.get("has_attachment"):
            continue

        # Gather surrounding context
        before_start = max(0, i - context_window)
        after_end = min(len(messages), i + context_window + 1)

        context_before = []
        for j in range(before_start, i):
            m = messages[j]
            ts = m["timestamp"].strftime("%H:%M") if m.get("timestamp") else ""
            context_before.append(f"[{ts}] {m['sender']}: {m['message']}")

        context_after = []
        for j in range(i + 1, after_end):
            m = messages[j]
            ts = m["timestamp"].strftime("%H:%M") if m.get("timestamp") else ""
            context_after.append(f"[{ts}] {m['sender']}: {m['message']}")

        media_refs.append({
            "index": i,
            "timestamp": msg["timestamp"],
            "sender": msg["sender"],
            "attachment_info": msg["attachment_info"],
            "context_before": context_before,
            "context_after": context_after,
            "message_hash": msg.get("message_hash", ""),
        })

    return media_refs


def match_media_files_to_messages(
    media_refs: List[Dict],
    media_filenames: List[str],
    tolerance_minutes: int = 2,
) -> List[Dict]:
    """
    Match media files from a zip export to <Multimedia omitido> messages by timestamp.

    WhatsApp media filenames follow patterns like:
      - "WhatsApp Image 2026-02-27 at 18.01.26.jpeg"
      - "WhatsApp Video 2026-02-27 at 18.01.26.mp4"
      - "WhatsApp Audio 2026-02-27 at 18.01.26.opus"

    Returns the media_refs list with an added "matched_file" key.
    """
    # Parse timestamps from media filenames
    file_timestamps = []
    for fname in media_filenames:
        ts = _parse_media_filename_timestamp(fname)
        file_timestamps.append((fname, ts))

    # For each unmatched media ref (no filename), try to match by timestamp
    tolerance = timedelta(minutes=tolerance_minutes)
    used_files = set()

    for ref in media_refs:
        ref["matched_file"] = None

        # If the attachment already has a filename from the chat text, skip matching
        if ref["attachment_info"].get("filename"):
            continue

        ref_ts = ref.get("timestamp")
        if not ref_ts:
            continue

        # Find closest file by timestamp
        best_match = None
        best_delta = None

        for fname, file_ts in file_timestamps:
            if file_ts is None or fname in used_files:
                continue
            delta = abs(ref_ts - file_ts)
            if delta <= tolerance:
                if best_delta is None or delta < best_delta:
                    best_match = fname
                    best_delta = delta

        if best_match:
            ref["matched_file"] = best_match
            used_files.add(best_match)

    return media_refs


def _parse_media_filename_timestamp(filename: str) -> Optional[datetime]:
    """
    Parse timestamp from WhatsApp media filenames.
    Examples:
      "WhatsApp Image 2026-02-27 at 18.01.26.jpeg"
      "IMG-20260227-WA0001.jpg"
    """
    # Pattern: "WhatsApp <Type> YYYY-MM-DD at HH.MM.SS"
    match = re.search(
        r'(\d{4})-(\d{2})-(\d{2})\s+at\s+(\d{1,2})\.(\d{2})\.(\d{2})',
        filename
    )
    if match:
        try:
            return datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3)),
                int(match.group(4)), int(match.group(5)), int(match.group(6))
            )
        except ValueError:
            pass

    # Pattern: "IMG-YYYYMMDD-WA0001"
    match = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if match:
        try:
            return datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
        except ValueError:
            pass

    return None


def deduplicate_messages(
    new_messages: List[Dict],
    existing_hashes: set,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split messages into new (not seen before) and duplicates.

    Args:
        new_messages: Parsed messages from a fresh export
        existing_hashes: Set of message_hash values already in the database

    Returns: (new_only, duplicates)
    """
    new_only = []
    duplicates = []

    for msg in new_messages:
        h = msg.get("message_hash", "")
        if h in existing_hashes:
            duplicates.append(msg)
        else:
            new_only.append(msg)

    return new_only, duplicates


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

    # Collect hashes for dedup tracking
    hashes = [m.get("message_hash", "") for m in messages if m.get("message_hash")]

    return {
        "text": combined_text,
        "start_time": start_time,
        "end_time": end_time,
        "participants": participants,
        "message_count": len(messages),
        "message_hashes": hashes,
    }
