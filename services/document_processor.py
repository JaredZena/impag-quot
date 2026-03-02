"""
Orchestrates the full document processing pipeline:
1. Download file bytes from R2
2. Extract text
3. Chunk text
4. Generate embeddings (OpenAI ada-002)
5. Upsert to Pinecone
6. Store chunks in document_chunk table
7. Update file_metadata processing status
"""
import re
from sqlalchemy.orm import Session
from datetime import datetime, timezone, date

from models import FileMetadata, DocumentChunk
from services.r2_storage import get_r2_client
from services.text_extraction import extract_text, is_extractable
from services.chunking import chunk_text, estimate_token_count
from services.pinecone_service import upsert_document_chunks, delete_document_vectors
from rag_system_moved.embeddings import generate_embeddings
from config import r2_bucket_name


_MONTH_MAP = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
    'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4,
    'may': 5, 'jun': 6, 'jul': 7, 'ago': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12,
}

_DATE_PATTERNS = [
    # "Fecha: 15/01/2025" or "Fecha: 15-01-2025"
    (r'[Ff]echa\s*[:]\s*(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', 'dmy'),
    # "Date: 01/15/2025"
    (r'[Dd]ate\s*[:]\s*(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', 'dmy'),
    # "15 de enero de 2025" or "15 de enero, 2025"
    (r'(\d{1,2})\s+de\s+(\w+)\s+(?:de\s+|,\s*)(\d{4})', 'dMy'),
    # Standalone dates near top of document: "15/01/2025" or "15-01-2025"
    (r'(?:^|\n)\s*(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', 'dmy'),
]


def _extract_document_date(text: str) -> date | None:
    """Try to extract the most prominent date from document text using regex patterns."""
    # Only search in the first ~2000 chars (dates are usually near the top)
    header = text[:2000]

    for pattern, fmt in _DATE_PATTERNS:
        match = re.search(pattern, header)
        if not match:
            continue
        try:
            g1, g2, g3 = match.group(1), match.group(2), match.group(3)
            if fmt == 'dmy':
                day, month, year = int(g1), int(g2), int(g3)
            elif fmt == 'dMy':
                day = int(g1)
                month = _MONTH_MAP.get(g2.lower())
                if not month:
                    continue
                year = int(g3)
            else:
                continue

            if year < 100:
                year += 2000
            parsed = date(year, month, day)
            # Sanity check: date should be between 2000 and 2030
            if 2000 <= parsed.year <= 2030:
                return parsed
        except (ValueError, TypeError):
            continue

    return None


def download_file_from_r2(file_key: str) -> bytes:
    """Download file bytes from R2."""
    client = get_r2_client()
    response = client.get_object(Bucket=r2_bucket_name, Key=file_key)
    return response['Body'].read()


def process_document(file_id: int, db: Session) -> None:
    """
    Main pipeline entry point. Called as a background task after upload.
    Manages its own error handling and updates file_metadata status.
    """
    file_record = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_record:
        print(f"[Pipeline] File {file_id} not found, skipping")
        return

    # Check if file type is extractable
    if not is_extractable(file_record.content_type, file_record.original_filename):
        file_record.processing_status = 'skipped'
        file_record.processed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[Pipeline] File {file_id} ({file_record.content_type}) not extractable, skipped")
        return

    file_record.processing_status = 'processing'
    db.commit()

    try:
        # Step 1: Download from R2
        print(f"[Pipeline] Downloading file {file_id}: {file_record.original_filename}")
        file_bytes = download_file_from_r2(file_record.file_key)

        # Step 2: Extract text
        print(f"[Pipeline] Extracting text from {file_record.content_type}")
        extracted_text = extract_text(
            file_bytes, file_record.content_type, file_record.original_filename
        )

        if not extracted_text or not extracted_text.strip():
            file_record.processing_status = 'failed'
            file_record.processing_error = 'No text could be extracted'
            file_record.processed_at = datetime.now(timezone.utc)
            db.commit()
            return

        file_record.extracted_text = extracted_text

        # Step 2b: Auto-extract document_date if not already set
        if not file_record.document_date:
            try:
                auto_date = _extract_document_date(extracted_text)
                if auto_date:
                    file_record.document_date = auto_date
                    print(f"[Pipeline] Auto-extracted document date: {auto_date}")
            except Exception as e:
                print(f"[Pipeline] Date extraction failed (non-fatal): {e}")

        # Step 3: Chunk text (WhatsApp-aware)
        if file_record.category == 'whatsapp-chat':
            from services.whatsapp_parser import parse_whatsapp_chat, chunk_conversation
            messages = parse_whatsapp_chat(extracted_text)
            wa_chunks = chunk_conversation(messages, time_window_minutes=60, max_chunk_messages=30)
            chunks = [{"index": i, "text": c["text"]} for i, c in enumerate(wa_chunks)]
        else:
            chunks = chunk_text(extracted_text)

        print(f"[Pipeline] Created {len(chunks)} chunks from {len(extracted_text)} chars")

        if not chunks:
            file_record.processing_status = 'completed'
            file_record.chunk_count = 0
            file_record.processed_at = datetime.now(timezone.utc)
            db.commit()
            return

        # Step 4: Generate embeddings in batches
        print(f"[Pipeline] Generating embeddings for {len(chunks)} chunks")
        chunk_texts = [c["text"] for c in chunks]

        all_embeddings = []
        batch_size = 20
        for i in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[i:i + batch_size]
            batch_embeddings = generate_embeddings(batch)
            all_embeddings.extend(batch_embeddings)

        # Step 5: Upsert to Pinecone
        print(f"[Pipeline] Upserting to Pinecone namespace for category: {file_record.category}")
        metadata_base = {
            "original_filename": file_record.original_filename,
            "supplier_id": file_record.supplier_id,
            "uploaded_by_email": file_record.uploaded_by_email,
        }
        vector_ids = upsert_document_chunks(
            file_id=file_record.id,
            category=file_record.category,
            chunks=chunks,
            embeddings=all_embeddings,
            metadata_base=metadata_base,
        )

        # Step 6: Store chunks in DB
        for chunk, vector_id in zip(chunks, vector_ids):
            db_chunk = DocumentChunk(
                file_id=file_record.id,
                chunk_index=chunk["index"],
                chunk_text=chunk["text"],
                pinecone_vector_id=vector_id,
                token_count=estimate_token_count(chunk["text"]),
            )
            db.add(db_chunk)

        # Step 7: Update status
        file_record.processing_status = 'completed'
        file_record.chunk_count = len(chunks)
        file_record.processed_at = datetime.now(timezone.utc)
        file_record.processing_error = None
        db.commit()

        print(f"[Pipeline] Successfully processed file {file_id}: {len(chunks)} chunks")

        # Step 8: Logistics extraction for packaging documents
        if file_record.category == 'packaging-logistics':
            _extract_logistics(file_record.id, extracted_text, db)

    except Exception as e:
        print(f"[Pipeline] Error processing file {file_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        db.rollback()

        try:
            file_record = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
            if file_record:
                file_record.processing_status = 'failed'
                file_record.processing_error = str(e)[:500]
                file_record.processed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass


def _extract_logistics(file_id: int, extracted_text: str, db: Session) -> None:
    """Post-processing step for packaging-logistics documents."""
    try:
        from services.logistics_extractor import extract_logistics_data
        from models import LogisticsMetadata

        logistics_data = extract_logistics_data(extracted_text)
        if 'error' not in logistics_data:
            logistics_record = LogisticsMetadata(
                file_id=file_id,
                product_name=logistics_data.get('product_name'),
                quantity=logistics_data.get('quantity'),
                package_size=logistics_data.get('package_size'),
                package_type=logistics_data.get('package_type'),
                weight_kg=logistics_data.get('weight_kg'),
                dimensions=logistics_data.get('dimensions'),
                origin=logistics_data.get('origin'),
                destination=logistics_data.get('destination'),
                carrier=logistics_data.get('carrier'),
                tracking_number=logistics_data.get('tracking_number'),
                estimated_delivery=logistics_data.get('estimated_delivery'),
                cost=logistics_data.get('cost'),
                currency=logistics_data.get('currency', 'MXN'),
                extraction_confidence=logistics_data.get('confidence', 'medium'),
                raw_extraction=logistics_data,
            )
            db.add(logistics_record)
            db.commit()
            print(f"[Pipeline] Logistics data extracted for file {file_id}")
    except Exception as e:
        print(f"[Pipeline] Logistics extraction failed for file {file_id}: {e}")
