from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from sqlalchemy import or_
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, FileMetadata
from auth import verify_google_token
from services.r2_storage import (
    upload_file as r2_upload,
    generate_presigned_download_url,
    delete_file as r2_delete,
    build_file_key,
    VALID_CATEGORIES,
    MAX_FILE_SIZE,
)

router = APIRouter(prefix="/files", tags=["files"])


class FileMetadataResponse(BaseModel):
    id: int
    file_key: str
    original_filename: str
    content_type: str
    file_size_bytes: int
    category: str
    description: Optional[str]
    tags: Optional[str]
    supplier_id: Optional[int]
    quotation_id: Optional[int]
    task_id: Optional[int]
    uploaded_by_email: str
    uploaded_by_name: Optional[str]
    created_at: datetime
    last_updated: Optional[datetime]

    class Config:
        from_attributes = True


class FileDownloadResponse(BaseModel):
    url: str
    filename: str
    expires_in: int


class FileUpdateRequest(BaseModel):
    description: Optional[str] = None
    tags: Optional[str] = None
    category: Optional[str] = None
    supplier_id: Optional[int] = None
    quotation_id: Optional[int] = None
    task_id: Optional[int] = None


@router.post("/upload", response_model=FileMetadataResponse)
async def upload_file(
    file: UploadFile = File(...),
    category: str = Form("general"),
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

    # Create DB row first to get the auto-increment ID
    db_file = FileMetadata(
        file_key="pending",  # Will be updated after upload
        original_filename=file.filename or "unnamed",
        content_type=content_type,
        file_size_bytes=len(content),
        category=category,
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

    return db_file


@router.get("/", response_model=List[FileMetadataResponse])
async def list_files(
    category: Optional[str] = None,
    supplier_id: Optional[int] = None,
    quotation_id: Optional[int] = None,
    task_id: Optional[int] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """List files with optional filters."""
    query = db.query(FileMetadata).filter(FileMetadata.archived_at == None)

    if category:
        query = query.filter(FileMetadata.category == category)
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

    files = query.order_by(FileMetadata.created_at.desc()).offset(skip).limit(limit).all()
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

    db_file.archived_at = func.now()
    db.commit()

    return {"message": "File archived successfully"}
