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
from auth import verify_google_token

router = APIRouter(prefix="/quotations", tags=["quotations"])

class QuotationResponse(BaseModel):
    supplier: Optional[str]
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
    category_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Process a quotation PDF file and extract structured data.
    
    Args:
        file: PDF file to process
        category_id: Optional product category ID (if not provided, AI will auto-categorize)
    """
    if not file.filename.lower().endswith('.pdf'):
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
    category_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Process all PDF files in a directory and extract structured data.
    
    Args:
        folder_path: Path to the directory containing PDF files
        category_id: Optional product category ID (if not provided, AI will auto-categorize)
    """
    # Validate folder path exists
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {folder_path}")
    
    if not os.path.isdir(folder_path):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {folder_path}")
    
    # Find all PDF files in the directory (case-insensitive)
    pdf_pattern_lower = os.path.join(folder_path, "*.pdf")
    pdf_pattern_upper = os.path.join(folder_path, "*.PDF")
    pdf_files = glob.glob(pdf_pattern_lower) + glob.glob(pdf_pattern_upper)
    
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
            
            # Display product names
            if result.get('skus_generated'):
                print(f"   Product names:")
                for sku_info in result['skus_generated']:
                    print(f"     • {sku_info['product_name']}")
            
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