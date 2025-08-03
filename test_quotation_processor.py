import requests
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_quotation_processing():
    # API endpoint
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
        print("Sending request to process quotation...")
        response = requests.post(url, files=files, data=data)
        
        # Check response
        if response.status_code == 200:
            result = response.json()
            print("\nProcessing Results:")
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
    test_quotation_processing() 