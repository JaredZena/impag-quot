from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
import tempfile
import os
import glob
from models import get_db
from quotation_processor import QuotationProcessor
from config import claude_api_key
<<<<<<< HEAD
from auth import verify_google_token
=======
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50

router = APIRouter(prefix="/quotations", tags=["quotations"])

class QuotationResponse(BaseModel):
    supplier: str
    products_processed: int
    variants_created: int
    supplier_products_created: int
    skus_generated: list

class BatchQuotationResponse(BaseModel):
    total_files_processed: int
    successful_files: int
    failed_files: int
    results: List[QuotationResponse]
    errors: List[dict]

@router.post("/process", response_model=QuotationResponse)
async def process_quotation(
    file: UploadFile = File(...),
    category_id: Optional[int] = Form(3),
<<<<<<< HEAD
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
=======
    db: Session = Depends(get_db)
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50
):
    """
    Process a quotation PDF file and extract structured data.
    
    Args:
        file: The PDF file to process
        category_id: Optional product category ID (default: 3)
        
    Returns:
        Dict with processing results including SKU information
    """
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    
    # Create a temporary file to store the uploaded PDF
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_file_path = temp_file.name
    
    try:
        # Initialize the quotation processor
        processor = QuotationProcessor(claude_api_key)
        
        # Process the quotation
        results = processor.process_quotation(temp_file_path, category_id)
        
        return results
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

@router.post("/process-batch", response_model=BatchQuotationResponse)
async def process_quotation_batch(
    folder_path: str = Form(...),
    category_id: Optional[int] = Form(3),
<<<<<<< HEAD
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
=======
    db: Session = Depends(get_db)
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50
):
    """
    Process all PDF files in a directory and extract structured data.
    
    Args:
        folder_path: Path to the directory containing PDF files
        category_id: Optional product category ID (default: 3)
        
    Returns:
        Dict with batch processing results including individual file results
    """
    # Validate folder path exists
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {folder_path}")
    
    if not os.path.isdir(folder_path):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {folder_path}")
    
    # Find all PDF files in the directory
    pdf_pattern = os.path.join(folder_path, "*.pdf")
    pdf_files = glob.glob(pdf_pattern)
    
    if not pdf_files:
        raise HTTPException(status_code=400, detail=f"No PDF files found in directory: {folder_path}")
    
    # Initialize the quotation processor
    processor = QuotationProcessor(claude_api_key)
    
    batch_results = {
        "total_files_processed": len(pdf_files),
        "successful_files": 0,
        "failed_files": 0,
        "results": [],
        "errors": []
    }
    
    print(f"Processing {len(pdf_files)} PDF files from directory: {folder_path}")
    print("=" * 60)
    
    # Process each PDF file
    for i, pdf_path in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_path)
        print(f"\n[{i}/{len(pdf_files)}] Processing: {filename}")
        print("-" * 40)
        
        try:
            # Process the quotation
            result = processor.process_quotation(pdf_path, category_id)
            batch_results["results"].append(result)
            batch_results["successful_files"] += 1
            
            print(f"✅ Successfully processed: {filename}")
            print(f"   Supplier: {result['supplier']}")
            print(f"   Products: {result['products_processed']}")
            print(f"   Variants: {result['variants_created']}")
            
        except Exception as e:
            error_info = {
                "filename": filename,
                "file_path": pdf_path,
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
    
    return batch_results 