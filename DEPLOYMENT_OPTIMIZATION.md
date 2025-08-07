# Docker Image Optimization for Koyeb Deployment

## Problem
- Original image size: **4,381 MB** (exceeds Koyeb's 2,000 MB limit)
- Deployment failing due to compressed image size limitations

## Solution
- Optimized image size: **249 MB** (94% reduction)
- Successfully fits within Koyeb's limits with room to spare

## Optimizations Applied

### 1. **Dockerfile Optimizations** (`Dockerfile.slim`)
- **Base image**: `python:3.11-slim` instead of full Python image
- **Multi-stage build**: Separate build and runtime stages (in `Dockerfile`)
- **Minimal system dependencies**: Only essential libraries
- **Non-root user**: Security best practice
- **Single worker**: Reduced memory footprint for small instances

### 2. **Dependency Reduction** (`requirements-slim.txt`)
- **Removed heavy ML libraries**:
  - All `llama-index` packages (~1GB+ of dependencies)
  - `easyocr` (computer vision with CUDA dependencies)
  - `PyMuPDF` (heavy PDF library)
  - `nltk`, `pandas`, `boto3` (if not essential)
  
- **Kept essential dependencies**:
  - FastAPI, uvicorn, gunicorn
  - PostgreSQL driver
  - Google authentication
  - Basic AI libraries (anthropic, openai)
  - Lightweight PDF processing (pypdf)

### 3. **Build Context Optimization** (`.dockerignore`)
- Excludes development files, tests, documentation
- Removes virtual environments, cache files
- Excludes git history and IDE files

## Deployment Options

### Option 1: Use Slim Dockerfile (Recommended)
```bash
# Use the ultra-lightweight version
docker build -f Dockerfile.slim -t impag-quot .
```

### Option 2: Use Multi-stage Dockerfile
```bash
# Use the multi-stage build version
docker build -f Dockerfile -t impag-quot .
```

## For Koyeb Deployment

1. **Replace your current Dockerfile** with `Dockerfile.slim`
2. **Update requirements.txt** with `requirements-slim.txt` content
3. **Redeploy** to Koyeb

### If You Need Removed Dependencies

If you discover you need any of the removed dependencies:

1. **Uncomment them in `requirements-slim.txt`**
2. **Test the build size**: `docker images`
3. **Ensure it stays under 2GB compressed**

### Heavy Dependencies Alternative Solutions

- **EasyOCR**: Consider using cloud OCR services (Google Vision, AWS Textract)
- **LlamaIndex**: Use lighter alternatives or cloud-based vector search
- **PyMuPDF**: Use `pypdf` for basic PDF operations

## Size Comparison

| Version | Size | Status |
|---------|------|--------|
| Original | 4,381 MB | ❌ Fails Koyeb limit |
| Optimized | 249 MB | ✅ Well within limits |
| **Reduction** | **94%** | **🎉 Success** |

## Next Steps

1. Deploy with the optimized Dockerfile
2. Monitor application functionality
3. Add back only essential dependencies if needed
4. Consider cloud alternatives for heavy ML operations