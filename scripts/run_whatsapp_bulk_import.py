"""
Local script to run WhatsApp bulk import for large zip files
that exceed the HTTP upload limit.

Usage: python scripts/run_whatsapp_bulk_import.py <zip_path> [--skip-classification] [--skip-media]
"""
import sys
import os
import time
import zipfile

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal, FileMetadata
from services.whatsapp_parser import (
    parse_whatsapp_chat,
    chunk_conversation,
    extract_media_with_context,
    match_media_files_to_messages,
    deduplicate_messages,
)
from services.media_classifier import classify_media_batch, _classify_from_filename


def main():
    zip_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not zip_path or not os.path.exists(zip_path):
        print("Usage: python scripts/run_whatsapp_bulk_import.py <zip_path> [--skip-classification] [--skip-media]")
        sys.exit(1)

    skip_classification = "--skip-classification" in sys.argv
    skip_media = "--skip-media" in sys.argv
    skip_embed = "--skip-embed" in sys.argv

    print(f"=== WhatsApp Bulk Import ===")
    print(f"File: {zip_path}")
    print(f"Size: {os.path.getsize(zip_path) / 1024 / 1024:.1f} MB")
    print(f"Skip AI classification: {skip_classification}")
    print(f"Skip media upload: {skip_media}")
    print(f"Skip embeddings: {skip_embed}")
    print()

    # Step 1: Extract chat from zip
    print("[1/7] Extracting zip...")
    t0 = time.time()
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()

    chat_file = None
    media_filenames = []
    for n in names:
        if n.endswith('.txt') and not n.startswith('__MACOSX') and not n.startswith('.'):
            chat_file = n
        ext = os.path.splitext(n)[1].lower()
        if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
                   '.mp4', '.mov', '.avi', '.mp3', '.ogg', '.opus', '.m4a',
                   '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.xml', '.csv', '.pptx'}:
            media_filenames.append(n)

    if not chat_file:
        print("ERROR: No .txt chat file found in zip")
        sys.exit(1)

    chat_bytes = zf.read(chat_file)
    print(f"  Chat: {chat_file} ({len(chat_bytes) / 1024 / 1024:.1f} MB)")
    print(f"  Media files in zip: {len(media_filenames)}")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 2: Parse messages
    print("\n[2/7] Parsing messages...")
    t0 = time.time()
    text = chat_bytes.decode('utf-8', errors='replace')
    all_messages = parse_whatsapp_chat(text)
    print(f"  Total messages: {len(all_messages)}")
    attachments = [m for m in all_messages if m.get('has_attachment')]
    print(f"  Attachments: {len(attachments)}")
    print(f"  [{time.time() - t0:.1f}s]")

    # Step 3: Deduplication
    print("\n[3/7] Checking for duplicates...")
    t0 = time.time()
    db = SessionLocal()
    try:
        existing_hashes = _get_existing_hashes(chat_file, db)
        new_messages, duplicates = deduplicate_messages(all_messages, existing_hashes)
        print(f"  Existing hashes: {len(existing_hashes)}")
        print(f"  New messages: {len(new_messages)}")
        print(f"  Duplicates skipped: {len(duplicates)}")
        print(f"  [{time.time() - t0:.1f}s]")

        if not new_messages:
            print("\n  No new messages to process. Done!")
            return

        # Step 4: Extract media references with context
        print("\n[4/7] Extracting media references...")
        t0 = time.time()
        media_refs = extract_media_with_context(all_messages, context_window=3)
        new_hashes = {m.get("message_hash") for m in new_messages}
        new_media_refs = [r for r in media_refs if r.get("message_hash") in new_hashes]
        print(f"  Total media refs: {len(media_refs)}")
        print(f"  New media refs: {len(new_media_refs)}")

        # Match media files to messages
        if media_filenames:
            new_media_refs = match_media_files_to_messages(new_media_refs, media_filenames)
            matched = sum(1 for r in new_media_refs if r.get("matched_file"))
            print(f"  Matched to zip files: {matched}")
        print(f"  [{time.time() - t0:.1f}s]")

        # Step 5: Classify from filenames (no AI)
        print("\n[5/7] Classifying from filenames...")
        t0 = time.time()
        classified_local = 0
        needs_ai = 0
        for ref in new_media_refs:
            filename = ref["attachment_info"].get("filename")
            if filename:
                result = _classify_from_filename(filename)
                if result:
                    ref["classification"] = result
                    classified_local += 1
                else:
                    needs_ai += 1
            else:
                needs_ai += 1

        print(f"  Classified from filename: {classified_local}")
        print(f"  Need AI classification: {needs_ai}")

        if not skip_classification and needs_ai > 0:
            print(f"  Running AI classification on {min(needs_ai, 200)} items...")
            unclassified = [r for r in new_media_refs if "classification" not in r][:200]
            classify_media_batch(unclassified, chat_file, max_batch_size=50)
            ai_classified = sum(1 for r in unclassified if "classification" in r)
            print(f"  AI classified: {ai_classified}")
        print(f"  [{time.time() - t0:.1f}s]")

        # Step 6: Store chat + chunks
        print("\n[6/7] Storing chat and creating chunks...")
        t0 = time.time()
        participants = list(set(m["sender"] for m in all_messages))
        date_range = {}
        if all_messages[0].get("timestamp"):
            date_range["start"] = all_messages[0]["timestamp"].isoformat()
        if all_messages[-1].get("timestamp"):
            date_range["end"] = all_messages[-1]["timestamp"].isoformat()

        from services.r2_storage import upload_file as r2_upload, build_file_key

        db_file = FileMetadata(
            file_key="pending",
            original_filename=chat_file,
            content_type="text/plain",
            file_size_bytes=len(chat_bytes),
            category='whatsapp-chat',
            subtype='interno',
            description=f"WhatsApp Operaciones: {len(all_messages)} msgs, {len(new_messages)} nuevos. {len(participants)} participantes.",
            tags="operaciones,interno,semanal",
            uploaded_by_email="jaredzenahernandez@gmail.com",
            uploaded_by_name="Jared",
        )
        db.add(db_file)
        db.flush()

        file_key = build_file_key('whatsapp-chat', db_file.id, chat_file)
        r2_upload(file_key, chat_bytes, "text/plain")
        db_file.file_key = file_key
        db_file.extracted_text = text  # Store full text for dedup on re-import
        print(f"  FileMetadata ID: {db_file.id}")
        print(f"  Uploaded to R2: {file_key}")

        # Chunk new messages
        chunks = chunk_conversation(new_messages, time_window_minutes=60, max_chunk_messages=30)
        print(f"  Chunks created: {len(chunks)}")

        if not skip_embed and chunks:
            print(f"  Generating embeddings...")
            from services.pinecone_service import upsert_document_chunks
            from services.chunking import estimate_token_count
            from rag_system_moved.embeddings import generate_embeddings
            from models import DocumentChunk
            from datetime import datetime, timezone

            indexed_chunks = [{"index": i, "text": c["text"]} for i, c in enumerate(chunks)]
            chunk_texts = [c["text"] for c in indexed_chunks]

            all_embeddings = []
            batch_size = 20
            for i in range(0, len(chunk_texts), batch_size):
                batch = chunk_texts[i:i + batch_size]
                batch_embeddings = generate_embeddings(batch)
                all_embeddings.extend(batch_embeddings)
                if (i // batch_size) % 10 == 0:
                    print(f"    Embedded {i + len(batch)}/{len(chunk_texts)} chunks...")

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
            print(f"  Embedded {len(chunks)} chunks into Pinecone")
        else:
            db_file.processing_status = 'completed' if skip_embed else 'pending'
            db_file.chunk_count = len(chunks)

        db.commit()
        print(f"  [{time.time() - t0:.1f}s]")

        # Step 7: Summary
        print("\n[7/7] Summary")
        print(f"  {'='*50}")
        print(f"  Messages total:       {len(all_messages)}")
        print(f"  Messages new:         {len(new_messages)}")
        print(f"  Messages skipped:     {len(duplicates)}")
        print(f"  Media refs:           {len(new_media_refs)}")
        print(f"  Classified (local):   {classified_local}")
        print(f"  Classified (AI):      {sum(1 for r in new_media_refs if 'classification' in r) - classified_local}")
        print(f"  Chunks embedded:      {db_file.chunk_count}")
        print(f"  File ID:              {db_file.id}")
        print(f"  {'='*50}")

        # Print sample classifications
        print("\n  Sample classifications:")
        for ref in new_media_refs[:10]:
            cls = ref.get("classification", {})
            fn = ref["attachment_info"].get("filename") or "(no filename)"
            label = cls.get("classification", "?") if isinstance(cls, dict) else "?"
            desc = cls.get("description", "")[:60] if isinstance(cls, dict) else ""
            print(f"    {label:20s} | {fn[:40]:40s} | {desc}")

    finally:
        db.close()


def _get_existing_hashes(chat_name: str, db) -> set:
    """Get message hashes from previously imported versions of this chat."""
    existing_files = db.query(FileMetadata).filter(
        FileMetadata.original_filename == chat_name,
        FileMetadata.category == 'whatsapp-chat',
        FileMetadata.processing_status == 'completed',
    ).all()

    if not existing_files:
        return set()

    hashes = set()
    for f in existing_files:
        if f.extracted_text:
            msgs = parse_whatsapp_chat(f.extracted_text)
            for m in msgs:
                h = m.get("message_hash")
                if h:
                    hashes.add(h)
    return hashes


if __name__ == "__main__":
    main()
