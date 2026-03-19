"""
WhatsApp Bulk Import Orchestrator.
Handles zip extraction, deduplication, media matching, and classification.
"""
import io
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from models import FileMetadata, DocumentChunk
from services.whatsapp_parser import (
    parse_whatsapp_chat,
    chunk_conversation,
    extract_media_with_context,
    match_media_files_to_messages,
    deduplicate_messages,
)
from services.media_classifier import classify_media_batch, classify_conversation
from services.r2_storage import upload_file as r2_upload, build_file_key
from services.document_processor import process_document
from services.pinecone_service import upsert_document_chunks
from services.chunking import estimate_token_count
from rag_system_moved.embeddings import generate_embeddings


# File extensions we care about
CHAT_EXTENSIONS = {'.txt'}
MEDIA_EXTENSIONS = {
    '.jpeg', '.jpg', '.png', '.gif', '.bmp', '.webp',  # images
    '.mp4', '.mov', '.avi', '.3gp',                      # video
    '.mp3', '.ogg', '.opus', '.m4a', '.aac',              # audio
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv',     # documents
    '.xml', '.pptx',                                       # other docs
}


def process_whatsapp_zip(
    zip_bytes: bytes,
    uploaded_by_email: str,
    uploaded_by_name: Optional[str],
    db: Session,
    skip_classification: bool = False,
) -> Dict:
    """
    Main entry point for bulk WhatsApp import from a zip file.

    Returns: {
        "conversations": [{chat_name, file_id, messages_total, messages_new, ...}],
        "media_files": [{filename, classification, linked_to_chat, ...}],
        "summary": {conversations_count, messages_total, messages_new, media_total, ...}
    }
    """
    # Step 1: Extract zip contents
    chat_files, media_files = _extract_zip(zip_bytes)

    if not chat_files:
        return {"error": "No se encontraron archivos de chat (.txt) en el zip"}

    results = {
        "conversations": [],
        "media_files_processed": 0,
        "media_files_classified": 0,
        "summary": {},
    }

    total_messages = 0
    total_new = 0
    total_media = 0

    for chat_name, chat_bytes in chat_files:
        conv_result = _process_single_chat(
            chat_name=chat_name,
            chat_bytes=chat_bytes,
            media_filenames=[name for name, _ in media_files],
            media_files_map={name: data for name, data in media_files},
            uploaded_by_email=uploaded_by_email,
            uploaded_by_name=uploaded_by_name,
            db=db,
            skip_classification=skip_classification,
        )
        results["conversations"].append(conv_result)
        total_messages += conv_result.get("messages_total", 0)
        total_new += conv_result.get("messages_new", 0)
        total_media += conv_result.get("media_refs_count", 0)

    # Store unmatched media files
    media_stored = _store_unmatched_media(
        media_files=media_files,
        uploaded_by_email=uploaded_by_email,
        uploaded_by_name=uploaded_by_name,
        db=db,
    )
    results["media_files_processed"] = media_stored

    results["summary"] = {
        "conversations_count": len(chat_files),
        "messages_total": total_messages,
        "messages_new": total_new,
        "messages_skipped": total_messages - total_new,
        "media_refs_found": total_media,
        "media_files_in_zip": len(media_files),
        "media_files_stored": media_stored,
    }

    return results


def process_whatsapp_txt(
    txt_bytes: bytes,
    filename: str,
    uploaded_by_email: str,
    uploaded_by_name: Optional[str],
    db: Session,
    skip_classification: bool = False,
) -> Dict:
    """
    Process a single .txt WhatsApp export (no media files).
    """
    return _process_single_chat(
        chat_name=filename,
        chat_bytes=txt_bytes,
        media_filenames=[],
        media_files_map={},
        uploaded_by_email=uploaded_by_email,
        uploaded_by_name=uploaded_by_name,
        db=db,
        skip_classification=skip_classification,
    )


def _extract_zip(zip_bytes: bytes) -> Tuple[List[Tuple[str, bytes]], List[Tuple[str, bytes]]]:
    """
    Extract chat files and media files from a zip.
    Returns: (chat_files, media_files) as lists of (filename, bytes) tuples.
    """
    chat_files = []
    media_files = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            # Skip macOS resource forks
            basename = os.path.basename(info.filename)
            if basename.startswith('.') or basename.startswith('__MACOSX'):
                continue

            ext = os.path.splitext(basename)[1].lower()
            data = zf.read(info.filename)

            if ext in CHAT_EXTENSIONS:
                chat_files.append((basename, data))
            elif ext in MEDIA_EXTENSIONS:
                media_files.append((basename, data))

    return chat_files, media_files


def _process_single_chat(
    chat_name: str,
    chat_bytes: bytes,
    media_filenames: List[str],
    media_files_map: Dict[str, bytes],
    uploaded_by_email: str,
    uploaded_by_name: Optional[str],
    db: Session,
    skip_classification: bool = False,
) -> Dict:
    """Process a single WhatsApp chat file."""
    text = chat_bytes.decode('utf-8', errors='replace')

    # Step 1: Parse all messages
    all_messages = parse_whatsapp_chat(text)
    if not all_messages:
        return {"chat_name": chat_name, "error": "No messages could be parsed", "messages_total": 0}

    # Step 2: Deduplication — find existing hashes for this chat
    existing_hashes = _get_existing_hashes(chat_name, db)
    new_messages, duplicates = deduplicate_messages(all_messages, existing_hashes)

    # Step 3: Extract media references with context (from ALL messages for full context)
    media_refs = extract_media_with_context(all_messages, context_window=3)

    # Filter to only new media refs
    new_hashes = {m.get("message_hash") for m in new_messages}
    new_media_refs = [r for r in media_refs if r.get("message_hash") in new_hashes]

    # Step 4: Match media files from zip to messages
    if media_filenames:
        new_media_refs = match_media_files_to_messages(new_media_refs, media_filenames)

    # Step 5: Classify media (unless skipped)
    conversation_classification = None
    if not skip_classification and new_messages:
        # Classify the conversation
        conversation_classification = classify_conversation(all_messages, chat_name)

        # Classify media files
        if new_media_refs:
            new_media_refs = classify_media_batch(new_media_refs, chat_name)

    # Step 6: Store the chat file + chunks in DB
    participants = list(set(m["sender"] for m in all_messages))
    date_range = {}
    if all_messages:
        if all_messages[0].get("timestamp"):
            date_range["start"] = all_messages[0]["timestamp"].isoformat()
        if all_messages[-1].get("timestamp"):
            date_range["end"] = all_messages[-1]["timestamp"].isoformat()

    # Build description
    desc_parts = [f"WhatsApp: {len(all_messages)} msgs"]
    if conversation_classification:
        summary = conversation_classification.get("summary", "")
        if summary:
            desc_parts.append(summary)

    # Build tags
    tags_list = []
    if conversation_classification:
        tags_list.extend(conversation_classification.get("tags", []))
        ctype = conversation_classification.get("conversation_type")
        if ctype:
            tags_list.append(ctype)

    # Store as FileMetadata
    db_file = FileMetadata(
        file_key="pending",
        original_filename=chat_name,
        content_type="text/plain",
        file_size_bytes=len(chat_bytes),
        category='whatsapp-chat',
        subtype=conversation_classification.get("customer_type") if conversation_classification else None,
        description=". ".join(desc_parts)[:500],
        tags=",".join(tags_list)[:500] if tags_list else None,
        uploaded_by_email=uploaded_by_email,
        uploaded_by_name=uploaded_by_name,
    )
    db.add(db_file)
    db.flush()

    # Upload raw txt to R2
    file_key = build_file_key('whatsapp-chat', db_file.id, chat_name)
    r2_upload(file_key, chat_bytes, "text/plain")
    db_file.file_key = file_key

    # Step 7: Chunk only NEW messages and embed into Pinecone
    if new_messages:
        chunks = chunk_conversation(new_messages, time_window_minutes=60, max_chunk_messages=30)
        _embed_and_store_chunks(db_file, chunks, db)
    else:
        db_file.processing_status = 'completed'
        db_file.chunk_count = 0
        db_file.processed_at = datetime.now(timezone.utc)

    # Step 8: Store media files that were matched
    media_stored = 0
    for ref in new_media_refs:
        matched_file = ref.get("matched_file")
        if matched_file and matched_file in media_files_map:
            _store_media_file(
                filename=matched_file,
                file_bytes=media_files_map[matched_file],
                classification=ref.get("classification"),
                parent_chat_id=db_file.id,
                uploaded_by_email=uploaded_by_email,
                uploaded_by_name=uploaded_by_name,
                db=db,
            )
            media_stored += 1

    db.commit()

    return {
        "chat_name": chat_name,
        "file_id": db_file.id,
        "messages_total": len(all_messages),
        "messages_new": len(new_messages),
        "messages_skipped": len(duplicates),
        "participants": participants,
        "date_range": date_range,
        "media_refs_count": len(media_refs),
        "new_media_refs": len(new_media_refs),
        "media_files_stored": media_stored,
        "conversation_classification": conversation_classification,
        "media_classifications": [
            {
                "filename": r["attachment_info"].get("filename") or r.get("matched_file"),
                "classification": r.get("classification", {}),
            }
            for r in new_media_refs
            if r.get("classification")
        ],
    }


def _get_existing_hashes(chat_name: str, db: Session) -> set:
    """
    Get message hashes already stored for this chat.
    We store hashes in the chunk text metadata (in the message_hashes field of chunks).
    """
    # Find existing FileMetadata records for this chat
    existing_files = db.query(FileMetadata).filter(
        FileMetadata.original_filename == chat_name,
        FileMetadata.category == 'whatsapp-chat',
        FileMetadata.processing_status == 'completed',
    ).all()

    if not existing_files:
        return set()

    hashes = set()
    for f in existing_files:
        # Read chunks and extract hashes from chunk text
        chunks = db.query(DocumentChunk).filter(
            DocumentChunk.file_id == f.id
        ).all()
        for chunk in chunks:
            # The chunk text contains formatted messages; we need to re-parse
            # to get hashes. Instead, store hashes as metadata.
            # For now, re-extract from the raw text stored in file.
            pass

    # Fallback: re-parse the stored extracted_text to get hashes
    for f in existing_files:
        if f.extracted_text:
            msgs = parse_whatsapp_chat(f.extracted_text)
            for m in msgs:
                h = m.get("message_hash")
                if h:
                    hashes.add(h)

    return hashes


def _embed_and_store_chunks(
    db_file: FileMetadata,
    chunks: List[Dict],
    db: Session,
) -> None:
    """Generate embeddings for chunks and store in Pinecone + DB."""
    if not chunks:
        db_file.processing_status = 'completed'
        db_file.chunk_count = 0
        db_file.processed_at = datetime.now(timezone.utc)
        return

    try:
        db_file.processing_status = 'processing'

        # Store the raw text for future dedup
        all_text = "\n---\n".join(c["text"] for c in chunks)
        db_file.extracted_text = all_text

        # Prepare chunks for embedding
        indexed_chunks = [{"index": i, "text": c["text"]} for i, c in enumerate(chunks)]
        chunk_texts = [c["text"] for c in indexed_chunks]

        # Generate embeddings in batches
        all_embeddings = []
        batch_size = 20
        for i in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[i:i + batch_size]
            batch_embeddings = generate_embeddings(batch)
            all_embeddings.extend(batch_embeddings)

        # Upsert to Pinecone
        metadata_base = {
            "original_filename": db_file.original_filename,
            "uploaded_by_email": db_file.uploaded_by_email,
        }
        vector_ids = upsert_document_chunks(
            file_id=db_file.id,
            category='whatsapp-chat',
            chunks=indexed_chunks,
            embeddings=all_embeddings,
            metadata_base=metadata_base,
        )

        # Store chunks in DB
        for chunk, vector_id in zip(indexed_chunks, vector_ids):
            db_chunk = DocumentChunk(
                file_id=db_file.id,
                chunk_index=chunk["index"],
                chunk_text=chunk["text"],
                pinecone_vector_id=vector_id,
                token_count=estimate_token_count(chunk["text"]),
            )
            db.add(db_chunk)

        db_file.processing_status = 'completed'
        db_file.chunk_count = len(chunks)
        db_file.processed_at = datetime.now(timezone.utc)

    except Exception as e:
        print(f"[WhatsAppBulk] Embedding failed: {e}")
        db_file.processing_status = 'failed'
        db_file.processing_error = str(e)[:500]
        db_file.processed_at = datetime.now(timezone.utc)


def _store_media_file(
    filename: str,
    file_bytes: bytes,
    classification: Optional[Dict],
    parent_chat_id: int,
    uploaded_by_email: str,
    uploaded_by_name: Optional[str],
    db: Session,
) -> Optional[int]:
    """Store a media file extracted from a WhatsApp zip."""
    ext = os.path.splitext(filename)[1].lower()
    content_type = _guess_content_type(ext)
    category = _media_category(ext)

    desc = ""
    tags = ""
    if classification:
        desc = classification.get("description", "")[:500]
        tag_list = classification.get("tags", [])
        tag_list.append("whatsapp-import")
        tags = ",".join(tag_list)[:500]

    db_file = FileMetadata(
        file_key="pending",
        original_filename=filename,
        content_type=content_type,
        file_size_bytes=len(file_bytes),
        category=category,
        description=desc or f"WhatsApp media from chat #{parent_chat_id}",
        tags=tags or "whatsapp-import",
        uploaded_by_email=uploaded_by_email,
        uploaded_by_name=uploaded_by_name,
    )
    db.add(db_file)
    db.flush()

    file_key = build_file_key(category, db_file.id, filename)
    r2_upload(file_key, file_bytes, content_type)
    db_file.file_key = file_key
    db_file.processing_status = 'pending'

    return db_file.id


def _store_unmatched_media(
    media_files: List[Tuple[str, bytes]],
    uploaded_by_email: str,
    uploaded_by_name: Optional[str],
    db: Session,
) -> int:
    """Store media files from zip that weren't matched to any chat message."""
    # This is handled by the _store_media_file calls in _process_single_chat
    # Any remaining unmatched files could be stored here if needed
    return 0


def _guess_content_type(ext: str) -> str:
    """Guess MIME type from file extension."""
    mapping = {
        '.jpeg': 'image/jpeg', '.jpg': 'image/jpeg', '.png': 'image/png',
        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
        '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.avi': 'video/x-msvideo',
        '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.opus': 'audio/opus',
        '.m4a': 'audio/mp4', '.aac': 'audio/aac',
        '.pdf': 'application/pdf',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xls': 'application/vnd.ms-excel',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.xml': 'application/xml', '.csv': 'text/csv',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }
    return mapping.get(ext, 'application/octet-stream')


def _media_category(ext: str) -> str:
    """Map file extension to a valid FileMetadata category."""
    if ext in {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.pptx'}:
        return 'cotizacion'
    elif ext == '.xml':
        return 'factura'
    elif ext in {'.jpeg', '.jpg', '.png', '.gif', '.webp', '.bmp'}:
        return 'imagen-de-producto'
    else:
        return 'whatsapp-chat'
