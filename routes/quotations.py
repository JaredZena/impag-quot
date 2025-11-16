from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
import tempfile
import os
import glob
from models import get_db, Supplier, SupplierProduct
from quotation_processor import QuotationProcessor
from config import claude_api_key
from auth import verify_google_token

router = APIRouter(prefix="/quotations", tags=["quotations"])

class SupplierDetectionInfo(BaseModel):
    confidence: str  # "high", "medium", "low", "none"
    detected_name: str
    has_rfc: bool
    has_contact_info: bool
    warning: Optional[str] = None
    existing_supplier: bool

class SupplierInfo(BaseModel):
    id: int
    name: str
    detection_info: SupplierDetectionInfo
    products_count: int

class MultiSupplierDetectionInfo(BaseModel):
    suppliers_detected: List[SupplierDetectionInfo]
    overall_confidence: str  # "high", "medium", "low", "none"
    warnings: List[str]

class QuotationResponse(BaseModel):
    suppliers: dict  # Dict[str, SupplierInfo] but Pydantic prefers dict
    products_processed: int
    supplier_products_created: int
    supplier_product_ids: list  # List of created supplier product IDs for reassignment
    skus_generated: list
    supplier_detection: MultiSupplierDetectionInfo
    currency_info: dict  # Currency detection and conversion info

class BatchQuotationResponse(BaseModel):
    total_files_processed: int
    successful_files: int
    failed_files: int
    results: List[QuotationResponse]
    errors: List[dict]

@router.post("/process", response_model=QuotationResponse)
@router.post("process", response_model=QuotationResponse)  # Handle both /quotations/process and /quotations/process/ explicitly  
async def process_quotation(
    file: UploadFile = File(...),
    category_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Process a document with product information and extract structured data.
    
    Supports multiple document types and formats:
    - Traditional quotations/invoices (PDF, images)
    - WhatsApp conversation exports (.txt files)
    - Any document containing product and supplier information
    
    Each product can have its own supplier, allowing for complex multi-supplier documents.
    
    Args:
        file: Document file to process (supported: PDF, PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP, TXT)
        category_id: Optional product category ID (if not provided, AI will auto-categorize)
    
    Returns:
        - suppliers: Dict of detected suppliers with their detection confidence
        - products_processed: Number of products successfully processed
        - supplier_products_created: Number of supplier-product relationships created
        - skus_generated: List of generated SKUs for each product
        - supplier_detection: Overall detection confidence and warnings
    
    Note: Uses Claude Vision API for superior OCR accuracy on images. WhatsApp conversations 
    are processed directly as text with specialized parsing for informal product discussions.
    """
    # Check if file format is supported
    supported_extensions = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp', '.txt'}
    file_extension = os.path.splitext(file.filename.lower())[1]
    if file_extension not in supported_extensions:
        supported_formats = "PDF, PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP, TXT"
        raise HTTPException(status_code=400, detail=f"Unsupported file format. Supported formats: {supported_formats}")
    
    # Create a temporary file to store the uploaded file
    file_extension = os.path.splitext(file.filename.lower())[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_file_path = temp_file.name
    
    try:
        print(f"🔍 API: Starting quotation processing for file: {file.filename}")
        # Initialize the quotation processor
        processor = QuotationProcessor(claude_api_key)
        print("🔍 API: QuotationProcessor initialized")
        
        # Process the quotation
        print("🔍 API: Calling process_quotation...")
        results = processor.process_quotation(temp_file_path, category_id)
        print("🔍 API: process_quotation completed successfully")
        
        return results
        
    except Exception as e:
        print(f"❌ API: Error in process_quotation endpoint: {str(e)}")
        print(f"❌ API: Error type: {type(e).__name__}")
        import traceback
        print(f"❌ API: Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

@router.post("/process-batch", response_model=BatchQuotationResponse)
@router.post("process-batch", response_model=BatchQuotationResponse)  # Handle both /quotations/process-batch and /quotations/process-batch/ explicitly
async def process_quotation_batch(
    folder_path: str = Form(...),
    category_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Process all supported files in a directory and extract structured data.
    
    Args:
        folder_path: Path to the directory containing supported files (PDF, images, TXT)
        category_id: Optional product category ID (if not provided, AI will auto-categorize)
        
    Supported formats: PDF, PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP, TXT
    Note: This endpoint processes files in parallel for faster batch processing
    """
    # Validate folder path exists
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {folder_path}")
    
    if not os.path.isdir(folder_path):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {folder_path}")
    
    # Find all supported files in the directory (case-insensitive)
    supported_patterns = [
        "*.pdf", "*.PDF",
        "*.png", "*.PNG",
        "*.jpg", "*.JPG", "*.jpeg", "*.JPEG",
        "*.gif", "*.GIF",
        "*.bmp", "*.BMP",
        "*.tiff", "*.TIFF",
        "*.webp", "*.WEBP",
        "*.txt", "*.TXT"
    ]
    
    all_files = []
    for pattern in supported_patterns:
        all_files.extend(glob.glob(os.path.join(folder_path, pattern)))
    
    if not all_files:
        raise HTTPException(status_code=400, detail=f"No supported files found in directory: {folder_path}. Supported formats: PDF, PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP, TXT")
    
    # Initialize the quotation processor
    processor = QuotationProcessor(claude_api_key)
    
    batch_results = {
        "total_files_processed": len(all_files),
        "successful_files": 0,
        "failed_files": 0,
        "results": [],
        "errors": []
    }
    
    print(f"Processing {len(all_files)} files from directory: {folder_path}")
    print("=" * 60)
    
    # Process each file
    for i, file_path in enumerate(all_files, 1):
        filename = os.path.basename(file_path)
        print(f"\n[{i}/{len(all_files)}] Processing: {filename}")
        print("-" * 40)
        
        try:
            # Process the quotation
            result = processor.process_quotation(file_path, category_id)
            batch_results["results"].append(result)
            batch_results["successful_files"] += 1
            
            print(f"✅ Successfully processed: {filename}")
            print(f"   Supplier: {result['supplier']}")
            print(f"   Products: {result['products_processed']}")
            
            # Display product names
            if result.get('skus_generated'):
                print(f"   Product names:")
                for sku_info in result['skus_generated']:
                    print(f"     • {sku_info['product_name']}")
            
        except Exception as e:
            error_info = {
                "filename": filename,
                "file_path": file_path,
                "error": str(e)
            }
            batch_results["errors"].append(error_info)
            batch_results["failed_files"] += 1
            
            print(f"❌ Failed to process: {filename}")
            print(f"   Error: {str(e)}")
    
    print(f"\n" + "=" * 60)
    print(f"Batch Processing Complete:")
    print(f"   Total files: {batch_results['total_files_processed']}")
    print(f"   Successful: {batch_results['successful_files']}")
    print(f"   Failed: {batch_results['failed_files']}")
    
    # Show all processed products
    if batch_results['successful_files'] > 0:
        all_products = []
        for result in batch_results['results']:
            for sku_info in result.get('skus_generated', []):
                all_products.append(sku_info['product_name'])
        
        if all_products:
            print(f"   Products added to database:")
            for product_name in all_products:
                print(f"     • {product_name}")
    
    return batch_results

class SupplierReassignmentRequest(BaseModel):
    supplier_product_ids: List[int]
    new_supplier_id: int

class TextProcessingRequest(BaseModel):
    text_content: str
    category_id: Optional[int] = None

@router.post("/reassign-supplier")
async def reassign_supplier_for_products(
    request: SupplierReassignmentRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Reassign products to a different supplier after quotation processing.
    This is useful when supplier detection was poor and needs manual correction.
    """
    try:
        # Verify the new supplier exists
        new_supplier = db.query(Supplier).filter(Supplier.id == request.new_supplier_id).first()
        if not new_supplier:
            raise HTTPException(status_code=404, detail="New supplier not found")
        
        # Get all supplier-product relationships to update
        supplier_products = db.query(SupplierProduct).filter(
            SupplierProduct.id.in_(request.supplier_product_ids)
        ).all()
        
        if len(supplier_products) != len(request.supplier_product_ids):
            raise HTTPException(status_code=404, detail="Some supplier-product relationships not found")
        
        updated_count = 0
        for sp in supplier_products:
            # Check if a relationship between this product and new supplier already exists
            existing = db.query(SupplierProduct).filter(
                SupplierProduct.supplier_id == request.new_supplier_id,
                SupplierProduct.product_id == sp.product_id
            ).first()
            
            if existing:
                # If relationship exists, delete the old one and keep the existing
                db.delete(sp)
                updated_count += 1
            else:
                # Update the supplier_id
                sp.supplier_id = request.new_supplier_id
                updated_count += 1
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Successfully reassigned {updated_count} products to supplier '{new_supplier.name}'",
            "updated_count": updated_count,
            "new_supplier": {
                "id": new_supplier.id,
                "name": new_supplier.name
            }
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error reassigning supplier: {str(e)}")

@router.post("/process-text", response_model=QuotationResponse)
async def process_text_content(
    request: TextProcessingRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Process text content directly (WhatsApp conversations, Google Sheets data, etc.).
    
    This is the fastest method as it doesn't require file upload.
    Supports the same multi-supplier detection as file processing.
    
    Args:
        request: TextProcessingRequest with text_content and optional category_id
    
    Returns:
        Same format as file processing - suppliers, products, detection info
    """
    if not request.text_content.strip():
        raise HTTPException(status_code=400, detail="Text content cannot be empty")
    
    # Create a temporary text file to use with the existing processor
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_file:
        temp_file.write(request.text_content)
        temp_file_path = temp_file.name
    
    try:
        # Initialize the quotation processor
        processor = QuotationProcessor(claude_api_key)
        
        # Process the text content
        results = processor.process_quotation(temp_file_path, request.category_id)
        
        return results
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path) 