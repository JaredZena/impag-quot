# 🚀 Impag Quotation System (RAG + Shopify Integration)

## 📌 Overview

This Python-based application is a **Quotation System** that uses **Retrieval-Augmented Generation (RAG)** to generate quotations based on:

- A **product catalog** stored in **Pinecone**.
- **History of quotations** stored in Pinecone.
- **Live product prices** from a **Shopify store** (fetched dynamically).

## 🔧 Features

✅ **Retrieval-Augmented Generation (RAG)** for intelligent quotation generation.\
✅ **Shopify Price Integration** (fetches & matches product names with fuzzy search).\
✅ **Pinecone Vector Search** (stores past quotes & catalog for retrieval).\
✅ **FastAPI Backend** for handling API requests.\
✅ **OpenAI GPT-4 Integration** for generating responses.\
✅ **Fuzzy Matching** for better product name detection.\
✅ **Asynchronous Processing** for faster performance.

---

## 🚀 Getting Started

### 📥 1. Clone the Repository

```bash
git clone https://github.com/yourusername/impag-quot.git
cd impag-quot
```

### 🛠 2. Install Dependencies

Ensure you have **Python 3.9+** installed. Then, install dependencies:

```bash
pip install -r requirements.txt
```

### 🔑 3. Set Up Environment Variables

Create a `.env` file in the root directory:

```env
OPENAI_API_KEY=your-openai-api-key
PINECONE_API_KEY=your-pinecone-api-key
PINECONE_ENV=your-pinecone-environment
```

### ▶️ 4. Run the Application

To start the FastAPI server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

For production, use:

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

### 🛠 5. Test API Endpoints

#### **Check API is Running**

```bash
curl http://localhost:8000/query -X POST -H "Content-Type: application/json" -d '{"query": "Cotización para malla sombra 35%"}'
```

#### **Expected Response**

```json
{
  "response": "Aquí tienes una cotización basada en el catálogo de Impag..."
}
```

#### **Test Quotation Processing**

```bash
# Test single file processing
python test_quotation_processor.py

# Test batch processing
python test_batch_quotation_processor.py
```

**Note:** For batch processing, create a directory with PDF files and update the `test_directory` path in the test script.

---

## 📌 API Endpoints

### 🔹 `POST /query`

- **Description:** Generates a quotation based on the user's query.
- **Request Body:**
  ```json
  {
    "query": "Cotización para drones agrícolas"
  }
  ```
- **Response Example:**
  ```json
  {
    "response": "Aquí tienes una cotización basada en el catálogo de Impag..."
  }
  ```

### 🔹 `POST /quotations/process`

- **Description:** Process a single quotation PDF file and extract structured data.
- **Request:** Multipart form data with PDF file upload
- **Parameters:**
  - `file`: PDF file to process
  - `category_id`: Optional product category ID (default: 3)
- **Response Example:**
  ```json
  {
    "supplier": "Supplier Name",
    "products_processed": 5,
    "variants_created": 5,
    "supplier_products_created": 5,
    "skus_generated": [
      {
        "product_name": "Product Name",
        "base_sku": "PROD-001",
        "variant_sku": "PROD-001-VAR1",
        "ai_suggested": "PROD-001",
        "category_id": 3
      }
    ]
  }
  ```

### 🔹 `POST /quotations/process-batch`

- **Description:** Process all PDF files in a directory and extract structured data.
- **Request:** Form data with directory path
- **Parameters:**
  - `folder_path`: Path to directory containing PDF files
  - `category_id`: Optional product category ID (default: 3)
- **Response Example:**
  ```json
  {
    "total_files_processed": 3,
    "successful_files": 2,
    "failed_files": 1,
    "results": [
      {
        "supplier": "Supplier Name",
        "products_processed": 5,
        "variants_created": 5,
        "supplier_products_created": 5,
        "skus_generated": [...]
      }
    ],
    "errors": [
      {
        "filename": "failed_file.pdf",
        "file_path": "/path/to/failed_file.pdf",
        "error": "Error message"
      }
    ]
  }
  ```

---

## ⚡ Deployment

### 🐳 Docker Deployment

```dockerfile
FROM python:3.9
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "main:app"]
```

Run the container:

```bash
docker build -t impag-quot .
docker run -p 8000:8000 impag-quot
```

---

## 📌 Technologies Used

- **Python 3.9+**
- **FastAPI** (for API development)
- **OpenAI GPT-4** (for natural language responses)
- **Pinecone** (for storing historical quotes & catalog)
- **Shopify API** (for fetching live product prices)
- **Fuzzy Matching** (`rapidfuzz`) (for flexible product name detection)
- **Uvicorn & Gunicorn** (for production server management)

---

## ✨ Contributors

Developed by [Jared Zena](https://github.com/JaredZena). PRs and contributions are welcome! 🎉

---

## 🚀 Future Enhancements

-

---

### 📌 Contact

📧 Email: [jaredzenahernandez@gmail.com](mailto\:jaredzenahernandez@gmail.com)\
🔗 GitHub: [JaredZena](https://github.com/JaredZena)

