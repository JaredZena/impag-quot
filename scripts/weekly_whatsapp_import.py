#!/usr/bin/env python3
"""
Weekly WhatsApp Import — One command does everything.

Usage:
    python scripts/weekly_whatsapp_import.py /path/to/export1.zip [/path/to/export2.zip ...]
    python scripts/weekly_whatsapp_import.py ~/Downloads/WhatsApp*.zip
    python scripts/weekly_whatsapp_import.py /path/to/folder/   (processes all .zip and .txt files)

Accepts:
    - One or more .zip files (WhatsApp "Export with media")
    - One or more .txt files (WhatsApp "Export without media")
    - A folder path (processes all .zip and .txt files in it)

What it does per file:
    1. Extracts chat + media from zip
    2. Parses messages, deduplicates against previous imports
    3. Classifies media files from filenames (free, local)
    4. Uploads new media files to R2
    5. Stores everything in PostgreSQL
    6. Extracts text from PDFs/DOCX/XML (free)
    7. OCRs images with EasyOCR (free)
    8. Embeds all text into Pinecone for RAG search
    9. Auto-detects document dates

No API credits needed. Runs entirely locally.
"""
import glob
import os
import re
import sys
import time
import zipfile
import warnings

warnings.filterwarnings("ignore", "Unverified HTTPS")
warnings.filterwarnings("ignore", "pin_memory")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import r2_bucket_name

# ── Shared constants ─────────────────────────────────────────────

SKIP_EXT = {".vcf", ".opus", ".crdownload"}
MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".mp4", ".mov", ".avi",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xml",
    ".csv", ".pptx", ".pub", ".zip",
}
CONTENT_TYPE_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xml": "application/xml", ".csv": "text/csv", ".txt": "text/plain",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pub": "application/x-mspublisher", ".zip": "application/zip",
}
EXT_CATEGORY = {
    ".pdf": "cotizacion", ".docx": "cotizacion", ".doc": "cotizacion",
    ".xml": "factura", ".xlsx": "estado-de-cuenta", ".xls": "estado-de-cuenta",
    ".jpg": "imagen-de-producto", ".jpeg": "imagen-de-producto",
    ".png": "imagen-de-producto", ".webp": "imagen-de-producto",
}
FREE_DOC_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/xml",
    "text/plain",
}
PREFIX_RE = re.compile(r"^-?\d+-(.+)$")


def _detect_chat_name(zip_filename: str, chat_txt_name: str) -> str:
    """Extract a human-readable chat name from the zip or txt filename."""
    # "WhatsApp Chat - Operaciones - IMPAG (2).zip" → "Operaciones - IMPAG"
    # "Chat de WhatsApp con Anayeli Rosas.zip" → "Anayeli Rosas"
    name = os.path.splitext(os.path.basename(zip_filename))[0]
    # Remove trailing " (N)" duplicates
    name = re.sub(r"\s*\(\d+\)$", "", name)
    # Remove "WhatsApp Chat - " or "Chat de WhatsApp con "
    name = re.sub(r"^WhatsApp Chat\s*-\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^Chat de WhatsApp con\s*", "", name, flags=re.IGNORECASE)
    return name.strip() or chat_txt_name


def _detect_subtype(chat_name: str, participants: list) -> str:
    """Guess if this is an internal, client, or supplier chat."""
    internal_keywords = {"operaciones", "impag", "equipo", "admin"}
    name_lower = chat_name.lower()
    if any(kw in name_lower for kw in internal_keywords):
        return "interno"
    if len(participants) > 4:
        return "interno"
    return "cliente"


def process_single_file(file_path: str, file_index: int = 1, total_files: int = 1,
                        chat_name_override: str = None, sweep_pending: bool = True):
    """Process a single .zip or .txt WhatsApp export.

    sweep_pending: steps 5-6 process ALL pending whatsapp-import documents/images
    in the DB, not just this file's — set False for text-only imports (web sync)
    so they don't race a concurrently running zip import.
    """
    filename = os.path.basename(file_path)
    size_mb = os.path.getsize(file_path) / 1024 / 1024
    is_zip = file_path.lower().endswith(".zip")

    print(f"\n{'─' * 60}")
    print(f"  [{file_index}/{total_files}] {filename} ({size_mb:.0f} MB)")
    print(f"{'─' * 60}\n")

    t_start = time.time()

    # ── Step 1: Extract ──────────────────────────────────────────
    print("[1/6] Extracting...", flush=True)
    t0 = time.time()

    zf = None
    media_entries = []
    chat_name = ""

    if is_zip:
        zf = zipfile.ZipFile(file_path)
        chat_file = None
        txt_candidates = []
        for zn in zf.namelist():
            bn = os.path.basename(zn)
            if not bn or bn.startswith(".") or "__MACOSX" in zn:
                continue
            if bn.endswith(".txt"):
                txt_candidates.append(zn)
                continue
            m = PREFIX_RE.match(bn)
            clean = m.group(1) if m else bn
            ext = os.path.splitext(clean)[1].lower()
            if ext in SKIP_EXT:
                continue
            if ext in MEDIA_EXT:
                media_entries.append((zn, clean, ext))

        # The chat log is _chat.txt (iOS) or "WhatsApp Chat..." (Android);
        # any other .txt in the zip is a shared attachment, not the conversation.
        for zn in txt_candidates:
            bn = os.path.basename(zn).lower()
            if bn == "_chat.txt" or bn.startswith(("whatsapp chat", "chat de whatsapp")):
                chat_file = zn
                break
        if not chat_file and txt_candidates:
            chat_file = max(txt_candidates, key=lambda zn: zf.getinfo(zn).file_size)
        for zn in txt_candidates:
            if zn == chat_file:
                continue
            bn = os.path.basename(zn)
            m = PREFIX_RE.match(bn)
            media_entries.append((zn, m.group(1) if m else bn, ".txt"))

        if not chat_file:
            print("  ERROR: No .txt chat file found in zip. Skipping.")
            return
        chat_bytes = zf.read(chat_file)
        chat_txt_name = os.path.basename(chat_file)
        chat_name = chat_name_override or _detect_chat_name(filename, chat_txt_name)
    else:
        with open(file_path, "rb") as f:
            chat_bytes = f.read()
        chat_txt_name = filename
        chat_name = chat_name_override or _detect_chat_name(filename, filename)

    print(f"  Chat: {chat_name}")
    print(f"  Media files: {len(media_entries)}")
    print(f"  [{time.time() - t0:.1f}s]\n", flush=True)

    # ── Step 2: Parse & deduplicate ──────────────────────────────
    print("[2/6] Parsing and deduplicating...", flush=True)
    t0 = time.time()
    from services.whatsapp_parser import parse_whatsapp_chat, chunk_conversation, deduplicate_messages
    from models import SessionLocal, FileMetadata, DocumentChunk

    text = chat_bytes.decode("utf-8", errors="replace")
    all_messages = parse_whatsapp_chat(text)

    if not all_messages:
        print("  No messages parsed. Skipping.")
        return

    # Get existing hashes — match by chat_txt_name (the .txt filename inside the zip)
    db = SessionLocal()
    existing_files = db.query(FileMetadata).filter(
        FileMetadata.original_filename == chat_txt_name,
        FileMetadata.category == "whatsapp-chat",
        FileMetadata.processing_status == "completed",
    ).all()

    existing_hashes = set()
    for f in existing_files:
        if f.extracted_text:
            for m in parse_whatsapp_chat(f.extracted_text):
                h = m.get("message_hash")
                if h:
                    existing_hashes.add(h)
    db.close()

    new_messages, duplicates = deduplicate_messages(all_messages, existing_hashes)
    participants = list(set(m["sender"] for m in all_messages))
    subtype = _detect_subtype(chat_name, participants)

    print(f"  Total messages: {len(all_messages)}")
    print(f"  Already imported: {len(duplicates)}")
    print(f"  New messages: {len(new_messages)}")
    print(f"  Participants: {', '.join(participants[:5])}{'...' if len(participants) > 5 else ''}")
    print(f"  Type: {subtype}")
    print(f"  [{time.time() - t0:.1f}s]\n", flush=True)

    if not new_messages and not media_entries:
        elapsed = time.time() - t_start
        print(f"  Nothing new. Done in {elapsed:.0f}s.\n")
        return

    # ── Step 3: Store chat + embed new messages ──────────────────
    new_chunks_count = 0
    if new_messages:
        print("[3/6] Storing chat and embedding...", flush=True)
        t0 = time.time()
        from services.r2_storage import upload_file as r2_upload, build_file_key
        from services.pinecone_service import upsert_document_chunks
        from services.chunking import estimate_token_count
        from rag_system_moved.embeddings import generate_embeddings
        from datetime import datetime, timezone

        db = SessionLocal()
        db_file = FileMetadata(
            file_key="pending",
            original_filename=chat_txt_name,
            content_type="text/plain",
            file_size_bytes=len(chat_bytes),
            category="whatsapp-chat",
            subtype=subtype,
            description=f"WhatsApp {chat_name}: {len(all_messages)} msgs, {len(new_messages)} nuevos. {', '.join(participants[:3])}.",
            tags=f"whatsapp-import,{subtype},{chat_name.lower().replace(' ', '-')[:50]}",
            uploaded_by_email="jaredzenahernandez@gmail.com",
            uploaded_by_name="Jared",
        )
        db.add(db_file)
        db.flush()

        file_key = build_file_key("whatsapp-chat", db_file.id, chat_txt_name)
        r2_upload(file_key, chat_bytes, "text/plain")
        db_file.file_key = file_key
        db_file.extracted_text = text

        chunks = chunk_conversation(new_messages, time_window_minutes=60, max_chunk_messages=30)
        if chunks:
            indexed_chunks = [{"index": i, "text": c["text"]} for i, c in enumerate(chunks)]
            chunk_texts = [c["text"] for c in indexed_chunks]
            all_embeddings = []
            for i in range(0, len(chunk_texts), 20):
                all_embeddings.extend(generate_embeddings(chunk_texts[i:i + 20]))

            vector_ids = upsert_document_chunks(
                file_id=db_file.id, category="whatsapp-chat",
                chunks=indexed_chunks, embeddings=all_embeddings,
                metadata_base={"original_filename": db_file.original_filename, "uploaded_by_email": db_file.uploaded_by_email},
            )
            for chunk, vid in zip(indexed_chunks, vector_ids):
                db.add(DocumentChunk(
                    file_id=db_file.id, chunk_index=chunk["index"],
                    chunk_text=chunk["text"], pinecone_vector_id=vid,
                    token_count=estimate_token_count(chunk["text"]),
                ))

        db_file.processing_status = "completed"
        db_file.chunk_count = len(chunks)
        db_file.processed_at = datetime.now(timezone.utc)
        db.commit()
        new_chunks_count = len(chunks)
        db.close()

        print(f"  Chunks embedded: {new_chunks_count}")
        print(f"  [{time.time() - t0:.1f}s]\n", flush=True)
    else:
        print("[3/6] No new messages — skipping.\n", flush=True)

    # ── Step 4: Upload new media files ───────────────────────────
    new_media_count = 0
    if media_entries and zf:
        print("[4/6] Uploading media files...", flush=True)
        t0 = time.time()
        from services.r2_storage import upload_file as r2_upload, build_file_key
        from services.media_classifier import _classify_from_filename
        from datetime import datetime, timezone

        db = SessionLocal()
        existing_filenames = set(
            r[0] for r in db.query(FileMetadata.original_filename).filter(
                FileMetadata.tags.like("%whatsapp-import%")
            ).all()
        )
        db.close()

        new_media = [(zp, cn, ext) for zp, cn, ext in media_entries if cn not in existing_filenames]

        if not new_media:
            print(f"  All {len(media_entries)} files already uploaded.")
        else:
            print(f"  New: {len(new_media)} (skipping {len(media_entries) - len(new_media)} existing)")
            for zp, clean_name, ext in new_media:
                db = SessionLocal()
                try:
                    file_bytes = zf.read(zp)
                    content_type = CONTENT_TYPE_MAP.get(ext, "application/octet-stream")
                    local_cls = _classify_from_filename(clean_name)
                    if local_cls:
                        cat_map = {"cotizacion": "cotizacion", "factura": "factura", "factura_xml": "factura", "catalogo": "catalogo"}
                        category = cat_map.get(local_cls.get("classification", ""), EXT_CATEGORY.get(ext, "whatsapp-chat"))
                        desc = local_cls.get("description", "")[:500]
                        tags_list = local_cls.get("tags", [])
                    else:
                        category = EXT_CATEGORY.get(ext, "whatsapp-chat")
                        desc = f"WhatsApp {chat_name}: {clean_name}"
                        tags_list = []

                    tags_list.extend(["whatsapp-import", chat_name.lower().replace(" ", "-")[:30]])
                    db_file = FileMetadata(
                        file_key="pending", original_filename=clean_name,
                        content_type=content_type, file_size_bytes=len(file_bytes),
                        category=category, description=desc, tags=",".join(tags_list)[:500],
                        uploaded_by_email="jaredzenahernandez@gmail.com",
                        uploaded_by_name="Jared", processing_status="pending",
                    )
                    db.add(db_file)
                    db.flush()
                    file_key = build_file_key(category, db_file.id, clean_name)
                    r2_upload(file_key, file_bytes, content_type)
                    db_file.file_key = file_key
                    db.commit()
                    new_media_count += 1
                except:
                    try: db.rollback()
                    except: pass
                finally:
                    db.close()

                if new_media_count % 100 == 0 and new_media_count > 0:
                    print(f"    {new_media_count}/{len(new_media)} [{time.time() - t0:.0f}s]", flush=True)
            print(f"  Uploaded: {new_media_count}")
        print(f"  [{time.time() - t0:.1f}s]\n", flush=True)
    else:
        print("[4/6] No media files.\n", flush=True)

    # ── Step 5: Process documents ────────────────────────────────
    if not sweep_pending:
        total = time.time() - t_start
        print(f"[5/6] & [6/6] Skipped (text-only import).\n")
        print(f"  {chat_name}: {int(total // 60)}m{int(total % 60)}s — "
              f"{len(new_messages)} msgs, {new_chunks_count} chunks")
        if zf:
            zf.close()
        return
    print("[5/6] Processing documents...", flush=True)
    t0 = time.time()
    from services.document_processor import process_document

    db = SessionLocal()
    doc_ids = [f.id for f in db.query(FileMetadata).filter(
        FileMetadata.processing_status == "pending",
        FileMetadata.tags.like("%whatsapp-import%"),
        FileMetadata.content_type.in_(FREE_DOC_TYPES),
    ).all()]
    db.close()

    doc_done = 0
    if not doc_ids:
        print("  No pending documents.")
    else:
        print(f"  {len(doc_ids)} documents...")
        for fid in doc_ids:
            db = SessionLocal()
            try:
                process_document(fid, db)
                doc_done += 1
            except: pass
            finally: db.close()
            if doc_done % 50 == 0 and doc_done > 0:
                print(f"    {doc_done}/{len(doc_ids)} [{time.time() - t0:.0f}s]", flush=True)
        print(f"  Done: {doc_done}/{len(doc_ids)}")
    print(f"  [{time.time() - t0:.1f}s]\n", flush=True)

    # ── Step 6: OCR images ───────────────────────────────────────
    print("[6/6] OCR-ing images...", flush=True)
    t0 = time.time()

    db = SessionLocal()
    img_ids = [r[0] for r in db.query(FileMetadata.id).filter(
        FileMetadata.processing_status == "pending",
        FileMetadata.tags.like("%whatsapp-import%"),
        FileMetadata.content_type.in_(["image/jpeg", "image/png", "image/webp"]),
    ).all()]
    db.close()

    ocr_done = 0
    ocr_text = 0
    ocr_embed = 0
    if not img_ids:
        print("  No pending images.")
    else:
        import easyocr
        from services.r2_storage import get_r2_client
        from services.pinecone_service import upsert_document_chunks
        from services.chunking import chunk_text, estimate_token_count
        from services.document_processor import _extract_document_date
        from rag_system_moved.embeddings import generate_embeddings
        from datetime import datetime, timezone

        print(f"  {len(img_ids)} images, loading EasyOCR...", flush=True)
        reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
        r2 = get_r2_client()

        for fid in img_ids:
            db = SessionLocal()
            try:
                f = db.query(FileMetadata).filter(FileMetadata.id == fid).first()
                if not f or f.processing_status != "pending":
                    db.close(); continue
                resp = r2.get_object(Bucket=r2_bucket_name, Key=f.file_key)
                img_bytes = resp["Body"].read()
                results = reader.readtext(img_bytes)
                extracted = " ".join([r[1] for r in results]).strip()
                f.processing_status = "completed"
                f.processed_at = datetime.now(timezone.utc)
                if len(extracted) > 20:
                    ocr_text += 1
                    f.extracted_text = extracted
                    doc_date = _extract_document_date(extracted)
                    if doc_date: f.document_date = doc_date
                    if len(extracted) > 50:
                        try:
                            chunks = chunk_text(extracted)
                            if chunks:
                                embs = generate_embeddings([c["text"] for c in chunks])
                                vids = upsert_document_chunks(
                                    file_id=f.id, category=f.category, chunks=chunks, embeddings=embs,
                                    metadata_base={"original_filename": f.original_filename, "uploaded_by_email": f.uploaded_by_email},
                                )
                                for chunk, vid in zip(chunks, vids):
                                    db.add(DocumentChunk(file_id=f.id, chunk_index=chunk["index"],
                                        chunk_text=chunk["text"], pinecone_vector_id=vid,
                                        token_count=estimate_token_count(chunk["text"])))
                                f.chunk_count = len(chunks)
                                ocr_embed += 1
                        except: f.chunk_count = 0
                else:
                    f.chunk_count = 0
                db.commit()
                ocr_done += 1
            except:
                ocr_done += 1
                try:
                    db.rollback()
                    f2 = db.query(FileMetadata).filter(FileMetadata.id == fid).first()
                    if f2: f2.processing_status = "failed"; db.commit()
                except: pass
            finally: db.close()
            if ocr_done % 50 == 0 and ocr_done > 0:
                print(f"    {ocr_done}/{len(img_ids)}: {ocr_text} text, {ocr_embed} embedded [{time.time() - t0:.0f}s]", flush=True)
        print(f"  Done: {ocr_done} processed, {ocr_text} text, {ocr_embed} embedded")
    print(f"  [{time.time() - t0:.1f}s]\n", flush=True)

    # ── Summary ──────────────────────────────────────────────────
    total = time.time() - t_start
    print(f"  {chat_name}: {int(total // 60)}m{int(total % 60)}s — "
          f"{len(new_messages)} msgs, {new_chunks_count} chunks, "
          f"{new_media_count} files, {doc_done} docs, {ocr_done} images")

    if zf:
        zf.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/weekly_whatsapp_import.py <files_or_folder>")
        print()
        print("Examples:")
        print("  python scripts/weekly_whatsapp_import.py ~/Downloads/WhatsApp*.zip")
        print("  python scripts/weekly_whatsapp_import.py ~/Downloads/wa-exports/")
        print("  python scripts/weekly_whatsapp_import.py chat1.zip chat2.zip chat3.txt")
        sys.exit(1)

    # Collect all input files
    input_files = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            input_files.extend(sorted(glob.glob(os.path.join(arg, "*.zip"))))
            input_files.extend(sorted(glob.glob(os.path.join(arg, "*.txt"))))
        elif os.path.isfile(arg):
            input_files.append(arg)
        else:
            # Could be a glob that the shell didn't expand
            expanded = sorted(glob.glob(arg))
            if expanded:
                input_files.extend(expanded)
            else:
                print(f"Not found: {arg}")

    input_files = [f for f in input_files if f.lower().endswith((".zip", ".txt"))]

    if not input_files:
        print("No .zip or .txt files found.")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"  Weekly WhatsApp Import")
    print(f"  Files: {len(input_files)}")
    for f in input_files:
        size = os.path.getsize(f) / 1024 / 1024
        print(f"    {os.path.basename(f)} ({size:.0f} MB)")
    print(f"{'=' * 60}")

    t_total = time.time()
    for i, fp in enumerate(input_files, 1):
        process_single_file(fp, i, len(input_files))

    elapsed = time.time() - t_total
    print(f"\n{'=' * 60}")
    print(f"  ALL DONE — {len(input_files)} file(s) in {int(elapsed // 60)}m{int(elapsed % 60)}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
