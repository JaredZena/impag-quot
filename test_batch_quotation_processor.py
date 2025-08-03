import requests
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_batch_quotation_processing():
    """Test the batch quotation processing endpoint."""
    
    # API endpoint for batch processing
    url = "http://localhost:8000/quotations/process-batch"
    
    # Test directory path - replace with your test directory containing PDF files
    test_directory = "./test_quotations"  # Change this to your actual directory path
    
    if not os.path.exists(test_directory):
        print(f"Error: Test directory not found at {test_directory}")
        print("Please create a directory with PDF files to test batch processing")
        return
    
    # Count PDF files in the directory
    pdf_files = [f for f in os.listdir(test_directory) if f.endswith('.pdf')]
    if not pdf_files:
        print(f"Error: No PDF files found in directory {test_directory}")
        return
    
    print(f"Found {len(pdf_files)} PDF files in directory: {test_directory}")
    print("Files to process:")
    for pdf_file in pdf_files:
        print(f"  - {pdf_file}")
    
    # Prepare the request data
    data = {
        'folder_path': test_directory,
        'category_id': 3  # Optional category ID
    }
    
    try:
        # Make the request
        print(f"\nSending batch processing request...")
        response = requests.post(url, data=data)
        
        # Check response
        if response.status_code == 200:
            result = response.json()
            print("\nBatch Processing Results:")
            print("=" * 60)
            print(f"Total files processed: {result['total_files_processed']}")
            print(f"Successful files: {result['successful_files']}")
            print(f"Failed files: {result['failed_files']}")
            
            # Display successful results
            if result['results']:
                print(f"\nSuccessful Processing Details:")
                print("-" * 40)
                for i, file_result in enumerate(result['results'], 1):
                    print(f"\nFile {i}:")
                    print(f"  Supplier: {file_result['supplier']}")
                    print(f"  Products Processed: {file_result['products_processed']}")
                    print(f"  Variants Created: {file_result['variants_created']}")
                    print(f"  Supplier Products Created: {file_result['supplier_products_created']}")
                    
                    # Display SKU generation details
                    if file_result['skus_generated']:
                        print(f"  SKUs Generated:")
                        for sku_info in file_result['skus_generated']:
                            print(f"    - {sku_info['product_name']}")
                            print(f"      AI Suggested: {sku_info['ai_suggested']}")
                            print(f"      Base SKU: {sku_info['base_sku']}")
                            print(f"      Variant SKU: {sku_info['variant_sku']}")
                            print(f"      Category: {sku_info['category_id']}")
            
            # Display errors if any
            if result['errors']:
                print(f"\nFailed Processing Details:")
                print("-" * 40)
                for error in result['errors']:
                    print(f"\nFile: {error['filename']}")
                    print(f"Error: {error['error']}")
                    
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"Error occurred: {str(e)}")

def test_single_file_processing():
    """Test the original single file processing endpoint for comparison."""
    
    # API endpoint for single file processing
    url = "http://localhost:8000/quotations/process"
    
    # Test PDF file path - replace with your test PDF
    pdf_path = "test_quotation.pdf"
    
    if not os.path.exists(pdf_path):
        print(f"Error: Test PDF file not found at {pdf_path}")
        return
    
    # Prepare the request
    files = {
        'file': ('test_quotation.pdf', open(pdf_path, 'rb'), 'application/pdf')
    }
    data = {
        'category_id': 3  # Optional category ID
    }
    
    try:
        # Make the request
        print("Sending single file processing request...")
        response = requests.post(url, files=files, data=data)
        
        # Check response
        if response.status_code == 200:
            result = response.json()
            print("\nSingle File Processing Results:")
            print("=" * 50)
            print(f"Supplier: {result['supplier']}")
            print(f"Products Processed: {result['products_processed']}")
            print(f"Supplier Products Created: {result['supplier_products_created']}")
            
            print("\nSKU Generation Details:")
            print("-" * 50)
            for sku_info in result['skus_generated']:
                print(f"\nProduct: {sku_info['product_name']}")
                print(f"  AI Suggested SKU: {sku_info['ai_suggested']}")
                print(f"  Base SKU: {sku_info['base_sku']}")
                print(f"  Product SKU: {sku_info['variant_sku']}")
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"Error occurred: {str(e)}")
    finally:
        # Close the file
        files['file'][1].close()

if __name__ == "__main__":
    print("Testing Quotation Processing System")
    print("=" * 60)
    
    # Test batch processing
    print("\n1. Testing Batch Processing:")
    test_batch_quotation_processing()
    
    # Test single file processing for comparison
    print("\n\n2. Testing Single File Processing:")
    test_single_file_processing() 