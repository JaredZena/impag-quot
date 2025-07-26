# cURL Examples for Quotation Processing

This file contains cURL examples for testing the quotation processing endpoints.

## Single File Processing

Process a single PDF file:

```bash
curl -X POST "http://localhost:8000/quotations/process" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@/path/to/your/quotation.pdf" \
  -F "category_id=3"
```

## Batch Processing

Process all PDF files in a directory:

```bash
curl -X POST "http://localhost:8000/quotations/process-batch" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "folder_path=/path/to/your/pdf/directory" \
  -d "category_id=3"
```

## Example with Specific Paths

### Single File (Windows)
```bash
curl -X POST "http://localhost:8000/quotations/process" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@C:\Users\YourName\Documents\quotations\quotation1.pdf" \
  -F "category_id=3"
```

### Single File (macOS/Linux)
```bash
curl -X POST "http://localhost:8000/quotations/process" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@/home/username/documents/quotations/quotation1.pdf" \
  -F "category_id=3"
```

### Batch Processing (Windows)
```bash
curl -X POST "http://localhost:8000/quotations/process-batch" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "folder_path=C:\Users\YourName\Documents\quotations" \
  -d "category_id=3"
```

### Batch Processing (macOS/Linux)
```bash
curl -X POST "http://localhost:8000/quotations/process-batch" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "folder_path=/home/username/documents/quotations" \
  -d "category_id=3"
```

## Testing with Different Categories

### Category 1 (Greenhouse Materials)
```bash
curl -X POST "http://localhost:8000/quotations/process-batch" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "folder_path=/path/to/quotations" \
  -d "category_id=1"
```

### Category 6 (Solar Energy Systems)
```bash
curl -X POST "http://localhost:8000/quotations/process-batch" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "folder_path=/path/to/quotations" \
  -d "category_id=6"
```

## Response Examples

### Single File Response
```json
{
  "supplier": "ABC Supplies Inc.",
  "products_processed": 5,
  "variants_created": 5,
  "supplier_products_created": 5,
  "skus_generated": [
    {
      "product_name": "Malla Sombra 35%",
      "base_sku": "MALLA-35",
      "variant_sku": "MALLA-35-4M",
      "ai_suggested": "MALLA-35",
      "category_id": 1
    }
  ]
}
```

### Batch Processing Response
```json
{
  "total_files_processed": 3,
  "successful_files": 2,
  "failed_files": 1,
  "results": [
    {
      "supplier": "ABC Supplies Inc.",
      "products_processed": 5,
      "variants_created": 5,
      "supplier_products_created": 5,
      "skus_generated": [...]
    },
    {
      "supplier": "XYZ Corporation",
      "products_processed": 3,
      "variants_created": 3,
      "supplier_products_created": 3,
      "skus_generated": [...]
    }
  ],
  "errors": [
    {
      "filename": "corrupted_file.pdf",
      "file_path": "/path/to/corrupted_file.pdf",
      "error": "Failed to extract text from PDF"
    }
  ]
}
```

## Error Handling

The batch processing endpoint will:
- Continue processing other files even if one fails
- Return detailed error information for failed files
- Provide a summary of successful vs failed processing
- Return HTTP 400 for invalid directory paths or missing PDF files
- Return HTTP 500 for server errors during processing 