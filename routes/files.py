from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from sqlalchemy import or_
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, date
from models import get_db, FileMetadata, DocumentChunk, SessionLocal
from auth import verify_google_token
from services.r2_storage import (
    upload_file as r2_upload,
    generate_presigned_download_url,
    generate_presigned_view_url,
    delete_file as r2_delete,
    build_file_key,
    VALID_CATEGORIES,
    MAX_FILE_SIZE,
)
from services.document_processor import process_document
from services.pinecone_service import delete_document_vectors, search_vectors, CATEGORY_TO_NAMESPACE
from rag_system_moved.embeddings import generate_embeddings

router = APIRouter(prefix="/files", tags=["files"])


class FileMetadataResponse(BaseModel):
    id: int
    file_key: str
    original_filename: str
    content_type: str
    file_size_bytes: int
    category: str
    subtype: Optional[str] = None
    document_date: Optional[date] = None
    description: Optional[str]
    tags: Optional[str]
    supplier_id: Optional[int]
    quotation_id: Optional[int]
    task_id: Optional[int]
    uploaded_by_email: str
    uploaded_by_name: Optional[str]
    created_at: datetime
    last_updated: Optional[datetime]
    processing_status: Optional[str] = None
    chunk_count: Optional[int] = None
    processing_error: Optional[str] = None
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FileDownloadResponse(BaseModel):
    url: str
    filename: str
    expires_in: int


class FileViewResponse(BaseModel):
    url: str
    filename: str
    content_type: str
    expires_in: int


class FileUpdateRequest(BaseModel):
    description: Optional[str] = None
    tags: Optional[str] = None
    category: Optional[str] = None
    subtype: Optional[str] = None
    document_date: Optional[date] = None
    supplier_id: Optional[int] = None
    quotation_id: Optional[int] = None
    task_id: Optional[int] = None


def _run_background_processing(file_id: int):
    """Background task wrapper that creates its own DB session."""
    db_session = SessionLocal()
    try:
        process_document(file_id, db_session)
    finally:
        db_session.close()


@router.post("/upload", response_model=FileMetadataResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form("cotizacion"),
    subtype: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    supplier_id: Optional[int] = Form(None),
    quotation_id: Optional[int] = Form(None),
    task_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Upload a file to R2 and create metadata record."""
    if category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
        )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    content_type = file.content_type or "application/octet-stream"

    parsed_doc_date = None
    if document_date:
        try:
            parsed_doc_date = date.fromisoformat(document_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid document_date format. Use YYYY-MM-DD.")

    # Create DB row first to get the auto-increment ID
    db_file = FileMetadata(
        file_key="pending",  # Will be updated after upload
        original_filename=file.filename or "unnamed",
        content_type=content_type,
        file_size_bytes=len(content),
        category=category,
        subtype=subtype,
        document_date=parsed_doc_date,
        description=description,
        tags=tags,
        supplier_id=supplier_id,
        quotation_id=quotation_id,
        task_id=task_id,
        uploaded_by_email=user["email"],
        uploaded_by_name=user.get("name"),
    )
    db.add(db_file)
    db.flush()  # Get the ID without committing

    file_key = build_file_key(category, db_file.id, file.filename or "unnamed")

    try:
        r2_upload(file_key, content, content_type)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to upload file to storage: {str(e)}")

    db_file.file_key = file_key
    db.commit()
    db.refresh(db_file)

    # Trigger async document processing
    background_tasks.add_task(_run_background_processing, db_file.id)

    return db_file


@router.get("/", response_model=List[FileMetadataResponse])
async def list_files(
    category: Optional[str] = None,
    subtype: Optional[str] = None,
    supplier_id: Optional[int] = None,
    quotation_id: Optional[int] = None,
    task_id: Optional[int] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """List files with optional filters."""
    query = db.query(FileMetadata).filter(FileMetadata.archived_at == None)

    if category:
        query = query.filter(FileMetadata.category == category)
    if subtype:
        query = query.filter(FileMetadata.subtype == subtype)
    if supplier_id is not None:
        query = query.filter(FileMetadata.supplier_id == supplier_id)
    if quotation_id is not None:
        query = query.filter(FileMetadata.quotation_id == quotation_id)
    if task_id is not None:
        query = query.filter(FileMetadata.task_id == task_id)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                FileMetadata.original_filename.ilike(search_term),
                FileMetadata.description.ilike(search_term),
                FileMetadata.tags.ilike(search_term),
            )
        )

    if sort_by == 'document_date_desc':
        query = query.order_by(FileMetadata.document_date.desc().nullslast(), FileMetadata.created_at.desc())
    elif sort_by == 'document_date_asc':
        query = query.order_by(FileMetadata.document_date.asc().nullslast(), FileMetadata.created_at.desc())
    else:
        query = query.order_by(FileMetadata.created_at.desc())

    files = query.offset(skip).limit(limit).all()
    return files


@router.get("/{file_id}", response_model=FileMetadataResponse)
async def get_file(
    file_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Get a single file's metadata."""
    db_file = db.query(FileMetadata).filter(
        FileMetadata.id == file_id,
        FileMetadata.archived_at == None,
    ).first()

    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    return db_file


@router.get("/{file_id}/download", response_model=FileDownloadResponse)
async def get_download_url(
    file_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Generate a presigned download URL for a file."""
    db_file = db.query(FileMetadata).filter(
        FileMetadata.id == file_id,
        FileMetadata.archived_at == None,
    ).first()

    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    expires_in = 900  # 15 minutes
    url = generate_presigned_download_url(db_file.file_key, expires_in)

    return FileDownloadResponse(
        url=url,
        filename=db_file.original_filename,
        expires_in=expires_in,
    )


@router.get("/{file_id}/view", response_model=FileViewResponse)
async def get_view_url(
    file_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Generate a presigned URL for inline viewing (Content-Disposition: inline)."""
    db_file = db.query(FileMetadata).filter(
        FileMetadata.id == file_id,
        FileMetadata.archived_at == None,
    ).first()

    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    expires_in = 900
    url = generate_presigned_view_url(db_file.file_key, db_file.content_type, expires_in)

    return FileViewResponse(
        url=url,
        filename=db_file.original_filename,
        content_type=db_file.content_type,
        expires_in=expires_in,
    )


@router.put("/{file_id}", response_model=FileMetadataResponse)
async def update_file(
    file_id: int,
    updates: FileUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Update file metadata."""
    db_file = db.query(FileMetadata).filter(
        FileMetadata.id == file_id,
        FileMetadata.archived_at == None,
    ).first()

    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    if updates.category is not None:
        if updates.category not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
            )
        db_file.category = updates.category

    if updates.subtype is not None:
        db_file.subtype = updates.subtype
    if updates.document_date is not None:
        db_file.document_date = updates.document_date
    if updates.description is not None:
        db_file.description = updates.description
    if updates.tags is not None:
        db_file.tags = updates.tags
    if updates.supplier_id is not None:
        db_file.supplier_id = updates.supplier_id
    if updates.quotation_id is not None:
        db_file.quotation_id = updates.quotation_id
    if updates.task_id is not None:
        db_file.task_id = updates.task_id

    db_file.last_updated = func.now()
    db.commit()
    db.refresh(db_file)

    return db_file


@router.delete("/{file_id}")
async def delete_file(
    file_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Soft delete a file (set archived_at)."""
    db_file = db.query(FileMetadata).filter(
        FileMetadata.id == file_id,
        FileMetadata.archived_at == None,
    ).first()

    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    # Always attempt to clean up Pinecone vectors (uses metadata filter, safe even if no vectors exist)
    try:
        delete_document_vectors(db_file.id, db_file.category, db_file.chunk_count or 0)
    except Exception as e:
        print(f"Warning: Failed to delete Pinecone vectors for file {file_id}: {e}")

    db_file.archived_at = func.now()
    db.commit()

    return {"message": "File archived successfully"}


# ─── Processing Status & Reprocess ─────────────────────────────


@router.get("/{file_id}/processing-status")
async def get_processing_status(
    file_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Check document processing status."""
    db_file = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "file_id": db_file.id,
        "processing_status": db_file.processing_status or 'pending',
        "chunk_count": db_file.chunk_count or 0,
        "processing_error": db_file.processing_error,
        "processed_at": db_file.processed_at,
    }


@router.post("/{file_id}/reprocess")
async def reprocess_file(
    file_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Re-trigger processing for a failed or pending file."""
    db_file = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")

    # Clean up existing vectors
    if db_file.chunk_count and db_file.chunk_count > 0:
        try:
            delete_document_vectors(db_file.id, db_file.category, db_file.chunk_count)
        except Exception:
            pass

    # Delete existing chunks from DB
    db.query(DocumentChunk).filter(DocumentChunk.file_id == file_id).delete()

    db_file.processing_status = 'pending'
    db_file.processing_error = None
    db_file.chunk_count = 0
    db_file.extracted_text = None
    db.commit()

    background_tasks.add_task(_run_background_processing, file_id)

    return {"message": "Reprocessing started", "file_id": file_id}


# ─── Semantic Search ────────────────────────────────────────────


class SemanticSearchRequest(BaseModel):
    query: str
    namespaces: Optional[List[str]] = None
    category: Optional[str] = None
    supplier_id: Optional[int] = None
    top_k: int = 10


class SearchResult(BaseModel):
    file_id: int
    original_filename: str
    category: str
    content_type: str
    score: float
    snippet: str
    chunk_index: int
    namespace: str
    supplier_id: Optional[int]


class SemanticSearchResponse(BaseModel):
    results: List[SearchResult]
    query: str
    total_results: int


@router.post("/search", response_model=SemanticSearchResponse)
async def semantic_search(
    request: SemanticSearchRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Semantic search across all documents using Pinecone."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    query_embedding = generate_embeddings([request.query])[0]

    namespaces = request.namespaces
    if request.category:
        ns = CATEGORY_TO_NAMESPACE.get(request.category)
        if ns:
            namespaces = [ns]

    filter_dict = None
    if request.supplier_id is not None:
        filter_dict = {"supplier_id": {"$eq": request.supplier_id}}

    matches = search_vectors(
        query_embedding=query_embedding,
        namespaces=namespaces,
        top_k=request.top_k,
        filter_dict=filter_dict,
    )

    # Batch-lookup content_type from DB for all matched file IDs
    # Also filters out orphaned Pinecone vectors whose files were deleted
    file_ids = list(set(match["metadata"].get("file_id", 0) for match in matches))
    ct_rows = db.query(FileMetadata.id, FileMetadata.content_type).filter(
        FileMetadata.id.in_(file_ids),
        FileMetadata.archived_at == None,
    ).all()
    content_type_map = {row.id: row.content_type for row in ct_rows}

    results = []
    for match in matches:
        meta = match["metadata"]
        fid = meta.get("file_id", 0)
        if fid not in content_type_map:
            continue  # Skip orphaned vectors (file deleted/archived)
        results.append(SearchResult(
            file_id=fid,
            original_filename=meta.get("original_filename", "Unknown"),
            category=meta.get("category", "general"),
            content_type=content_type_map[fid],
            score=round(match["score"], 4),
            snippet=meta.get("text", "")[:500],
            chunk_index=meta.get("chunk_index", 0),
            namespace=match["namespace"],
            supplier_id=meta.get("supplier_id"),
        ))

    return SemanticSearchResponse(
        results=results,
        query=request.query,
        total_results=len(results),
    )


# ─── Bulk Upload ────────────────────────────────────────────────


class BulkUploadResponse(BaseModel):
    total_files: int
    successful: int
    failed: int
    files: List[FileMetadataResponse]
    errors: List[dict]


@router.post("/upload-bulk", response_model=BulkUploadResponse)
async def upload_files_bulk(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    category: str = Form("cotizacion"),
    subtype: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    supplier_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Upload multiple files in a single request."""
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")

    parsed_doc_date = None
    if document_date:
        try:
            parsed_doc_date = date.fromisoformat(document_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid document_date format. Use YYYY-MM-DD.")

    successful_files = []
    errors = []

    for upload_file in files:
        try:
            content = await upload_file.read()
            if len(content) > MAX_FILE_SIZE:
                errors.append({"filename": upload_file.filename, "error": "File too large"})
                continue

            content_type = upload_file.content_type or "application/octet-stream"

            db_file = FileMetadata(
                file_key="pending",
                original_filename=upload_file.filename or "unnamed",
                content_type=content_type,
                file_size_bytes=len(content),
                category=category,
                subtype=subtype,
                document_date=parsed_doc_date,
                description=description,
                tags=tags,
                supplier_id=supplier_id,
                uploaded_by_email=user["email"],
                uploaded_by_name=user.get("name"),
            )
            db.add(db_file)
            db.flush()

            file_key = build_file_key(category, db_file.id, upload_file.filename or "unnamed")
            r2_upload(file_key, content, content_type)
            db_file.file_key = file_key
            db.commit()
            db.refresh(db_file)

            successful_files.append(db_file)
            background_tasks.add_task(_run_background_processing, db_file.id)

        except Exception as e:
            db.rollback()
            errors.append({"filename": upload_file.filename, "error": str(e)})

    return BulkUploadResponse(
        total_files=len(files),
        successful=len(successful_files),
        failed=len(errors),
        files=successful_files,
        errors=errors,
    )


# ─── WhatsApp Import ───────────────────────────────────────────


class WhatsAppImportResponse(BaseModel):
    file_id: int
    messages_parsed: int
    chunks_created: int
    participants: List[str]
    date_range: dict


@router.post("/import-whatsapp", response_model=WhatsAppImportResponse)
async def import_whatsapp_chat(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    supplier_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Import a WhatsApp .txt chat export."""
    if not file.filename or not file.filename.lower().endswith('.txt'):
        raise HTTPException(status_code=400, detail="WhatsApp exports must be .txt files")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large")

    from services.whatsapp_parser import parse_whatsapp_chat, chunk_conversation

    text = content.decode('utf-8', errors='replace')
    messages = parse_whatsapp_chat(text)
    chunks = chunk_conversation(messages)

    participants = list(set(m["sender"] for m in messages))
    date_range = {}
    if messages:
        if messages[0].get("timestamp"):
            date_range["start"] = messages[0]["timestamp"].isoformat()
        if messages[-1].get("timestamp"):
            date_range["end"] = messages[-1]["timestamp"].isoformat()

    content_type = "text/plain"
    db_file = FileMetadata(
        file_key="pending",
        original_filename=file.filename or "whatsapp-chat.txt",
        content_type=content_type,
        file_size_bytes=len(content),
        category='whatsapp-chat',
        description=description or f"WhatsApp chat: {len(messages)} mensajes, {len(participants)} participantes",
        tags=tags,
        supplier_id=supplier_id,
        uploaded_by_email=user["email"],
        uploaded_by_name=user.get("name"),
    )
    db.add(db_file)
    db.flush()

    file_key = build_file_key('whatsapp-chat', db_file.id, file.filename or "whatsapp-chat.txt")
    r2_upload(file_key, content, content_type)
    db_file.file_key = file_key
    db.commit()
    db.refresh(db_file)

    background_tasks.add_task(_run_background_processing, db_file.id)

    return WhatsAppImportResponse(
        file_id=db_file.id,
        messages_parsed=len(messages),
        chunks_created=len(chunks),
        participants=participants,
        date_range=date_range,
    )
