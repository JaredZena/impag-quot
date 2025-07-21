#!/usr/bin/env python3
"""
Example script demonstrating how to use the batch quotation processing endpoint.

This script shows how to:
1. Process all PDF files in a directory
2. Handle the response and display results
3. Process errors gracefully
"""

import requests
import os
import json
from typing import Dict, Any

def process_quotations_batch(folder_path: str, category_id: int = 3) -> Dict[str, Any]:
    """
    Process all PDF files in a directory using the batch processing endpoint.
    
    Args:
        folder_path: Path to directory containing PDF files
        category_id: Optional product category ID (default: 3)
        
    Returns:
        Dictionary containing batch processing results
    """
    
    # API endpoint
    url = "http://localhost:8000/quotations/process-batch"
    
    # Validate directory exists
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Directory not found: {folder_path}")
    
    if not os.path.isdir(folder_path):
        raise ValueError(f"Path is not a directory: {folder_path}")
    
    # Count PDF files
    pdf_files = [f for f in os.listdir(folder_path) if f.endswith('.pdf')]
    if not pdf_files:
        raise ValueError(f"No PDF files found in directory: {folder_path}")
    
    print(f"Found {len(pdf_files)} PDF files in {folder_path}")
    
    # Prepare request data
    data = {
        'folder_path': folder_path,
        'category_id': category_id
    }
    
    try:
        # Make the request
        print("Sending batch processing request...")
        response = requests.post(url, data=data)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"API request failed: {response.status_code} - {response.text}")
            
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error: {str(e)}")

def display_batch_results(results: Dict[str, Any]) -> None:
    """
    Display batch processing results in a formatted way.
    
    Args:
        results: Dictionary containing batch processing results
    """
    
    print("\n" + "="*60)
    print("BATCH PROCESSING RESULTS")
    print("="*60)
    
    # Summary
    print(f"📊 Summary:")
    print(f"   Total files processed: {results['total_files_processed']}")
    print(f"   ✅ Successful: {results['successful_files']}")
    print(f"   ❌ Failed: {results['failed_files']}")
    
    # Successful results
    if results['results']:
        print(f"\n📋 Successful Processing Details:")
        print("-" * 40)
        
        total_products = 0
        total_variants = 0
        total_supplier_products = 0
        
        for i, file_result in enumerate(results['results'], 1):
            print(f"\n📄 File {i}:")
            print(f"   Supplier: {file_result['supplier']}")
            print(f"   Products: {file_result['products_processed']}")
            print(f"   Variants: {file_result['variants_created']}")
            print(f"   Supplier Products: {file_result['supplier_products_created']}")
            
            # Accumulate totals
            total_products += file_result['products_processed']
            total_variants += file_result['variants_created']
            total_supplier_products += file_result['supplier_products_created']
            
            # Show SKU details
            if file_result['skus_generated']:
                print(f"   SKUs Generated:")
                for sku_info in file_result['skus_generated']:
                    print(f"     • {sku_info['product_name']}")
                    print(f"       Base SKU: {sku_info['base_sku']}")
                    print(f"       Variant SKU: {sku_info['variant_sku']}")
                    print(f"       Category: {sku_info['category_id']}")
        
        print(f"\n📈 Totals:")
        print(f"   Total Products: {total_products}")
        print(f"   Total Variants: {total_variants}")
        print(f"   Total Supplier Products: {total_supplier_products}")
    
    # Error details
    if results['errors']:
        print(f"\n❌ Failed Processing Details:")
        print("-" * 40)
        
        for error in results['errors']:
            print(f"\n📄 File: {error['filename']}")
            print(f"   Path: {error['file_path']}")
            print(f"   Error: {error['error']}")

def main():
    """Main function demonstrating batch processing usage."""
    
    # Configuration
    FOLDER_PATH = "./test_quotations"  # Change this to your directory path
    CATEGORY_ID = 3  # Optional: change category ID if needed
    
    print("🚀 Batch Quotation Processing Example")
    print("=" * 60)
    
    try:
        # Process all PDF files in the directory
        results = process_quotations_batch(FOLDER_PATH, CATEGORY_ID)
        
        # Display results
        display_batch_results(results)
        
        # Save results to file (optional)
        output_file = "batch_processing_results.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n💾 Results saved to: {output_file}")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main()) 