# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application
```bash
# Development server
uvicorn main:app --host 0.0.0.0 --port 8000

# Production server
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

### Testing
```bash
# Run tests for the quotation processor
python test_quotation_processor.py

# Run tests for batch processing
python test_batch_quotation_processor.py

# Run example batch processing script
python example_batch_processing.py
```

### Environment Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Required environment variables (.env file):
OPENAI_API_KEY=your-openai-api-key
PINECONE_API_KEY=your-pinecone-api-key
PINECONE_ENV=your-pinecone-environment
CLAUDE_API_KEY=your-claude-api-key
DATABASE_URL=your-database-url
```

### Web orders (`POST /storefront/orders`, the todoparaelcampo.com.mx checkout)
Set these in App Runner, never in the local `.env` (the image bakes it in). They are read per request.
- `STOREFRONT_ORDERS_API_KEY`: the dedicated key the storefront's Vercel functions send as `X-API-Key`. While it is unset the endpoint answers 503 (dark).
- `WEB_ORDER_NOTIFY_EMAILS`: comma-separated admin logins that get the `web_order_paid` / `web_order_pending` / `web_order_problem` notifications. Falls back to `WEB_ORDER_ASSIGNEE`.
- `WEB_ORDER_ASSIGNEE`: the task_user email for the "Por enviar" and "Solicitud de facturas" tasks, and `quote.assigned_to`.
- `WEB_ORDERS_ALLOW_TEST`: `true` records Mercado Pago TEST payments and checkouts (`live_mode=false`). Otherwise they get 200 `{"ignored":"test_mode"}`, are only logged, and never appear in the admin. Turn it off again after a sandbox test.
- `WEB_ORDERS_MAX_DRAFTS_PER_HOUR`: caps unpaid `checkout_created` drafts per hour. Default 100; `0` turns the cap off. Above the cap `checkout_created` gets 429. Payments are never capped.
- Buyer confirmation email (the order record and the póliza de garantía, LFPC arts. 52 and 78) needs `RESEND_API_KEY` and `WEB_ORDER_STORE_ADDRESS`, the store's physical address for pickups, claims and warranties. Without both, no email is sent and the fulfillment task tells staff to send it by hand. Optional: `WEB_ORDER_RETURN_ADDRESS` (defaults to the store address) and `WEB_ORDER_FROM_EMAIL` (default `Todo Para El Campo <ventas@todoparaelcampo.com.mx>`; the domain must be verified in Resend).

## Architecture Overview

### Core System Components

**RAG-based Quotation System**: The application generates intelligent quotations by combining Retrieval-Augmented Generation with multiple data sources:
- **Pinecone Vector Database**: Stores historical quotations and product catalog embeddings for context retrieval
- **Shopify Integration**: Fetches live product prices with fuzzy matching capabilities  
- **Claude AI Processing**: Extracts structured data from PDF quotations and generates responses
- **PostgreSQL Database**: Manages suppliers, products, variants, and quotation history

### Data Model Structure

**Multi-level Product Hierarchy**:
- `ProductCategory` → `Product` (base product with base_sku) → `ProductVariant` (specific SKU with specifications) → `SupplierProduct` (supplier-specific pricing/stock)
- Products use hybrid SKU generation combining AI suggestions with code-based fallbacks
- Support for units: PIEZA, KG, ROLLO, METRO with package sizing

**Key Relationships**:
- Suppliers can offer multiple variants of the same product at different prices
- Product variants store JSON specifications for flexible attribute storage
- Query history is tracked for improving RAG responses

### Core Modules

**main.py**: FastAPI application with CORS middleware, includes routers for suppliers, products, and quotations. Main `/query` endpoint handles RAG-based quotation generation.

**models.py**: SQLAlchemy models with PostgreSQL+psycopg2 configuration. Handles connection pooling and automatic table creation.

**rag_system.py**: Orchestrates the RAG pipeline by combining Pinecone historical context with live Shopify product data using OpenAI embeddings and Claude for response generation.

**quotation_processor.py**: Processes PDF and image quotations using Claude AI to extract structured supplier and product data. Features hybrid SKU generation, automatic category selection, and OCR text extraction from images using EasyOCR.

**routes/quotations.py**: Handles both single file uploads (`/process`) and batch directory processing (`/process-batch`) for quotation files. Supports PDF and image formats (PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP).

### API Integration Points

**Claude AI**: Used for text processing and structured data parsing with temperature=0 for consistency. Model: claude-sonnet-4-20250514

**EasyOCR**: Multi-language OCR engine for extracting text from images. Supports English and Spanish with confidence-based filtering.

**OpenAI**: Generates embeddings for product search and context matching in the RAG system

**Pinecone**: Vector search with top_k=7 for historical context retrieval

**Shopify**: Live product price fetching with fuzzy matching using rapidfuzz

### Data Flow

1. **Quotation Generation**: User query → OpenAI embedding → Pinecone context search → Shopify product matching → Claude response generation
2. **File Processing**: PDF/Image upload → text extraction (PyMuPDF for PDFs, EasyOCR for images) → Claude structured parsing → database storage with SKU generation
3. **Batch File Processing**: Directory path → find all supported files → process each file individually → aggregate results with error handling
4. **Product Management**: Base products → variants with specifications → supplier pricing relationships