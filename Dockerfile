# Optimized Docker build for Koyeb deployment
FROM python:3.11-slim

# Install essential system dependencies
RUN apt-get update && apt-get install -y \
    # For PostgreSQL
    libpq5 \
    # For Tesseract OCR
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    # For general image processing
    libjpeg62-turbo \
    libfreetype6 \
    # Cleanup to reduce image size
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Verify Tesseract installation and make it accessible
RUN which tesseract && tesseract --version

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app
USER app
WORKDIR /home/app

# Verify Tesseract is accessible to app user
RUN which tesseract || echo "Tesseract not in PATH for app user"

# Install Python dependencies
COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Add user's local bin to PATH
ENV PATH="/home/app/.local/bin:$PATH"

# Copy application code
COPY --chown=app:app . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run with reduced workers for memory efficiency
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "main:app", "--timeout", "120"]