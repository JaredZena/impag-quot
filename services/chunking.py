"""
Text chunking service for document processing pipeline.
Splits extracted text into overlapping chunks suitable for embedding.
"""
from typing import List, Dict

# Target ~400 tokens per chunk. Spanish averages ~3 chars/token.
# 1200 chars ≈ 400 tokens.
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Dict]:
    """
    Split text into overlapping chunks by character count.
    Tries to break at paragraph/sentence boundaries when possible.

    Returns: [{"index": 0, "text": "...", "char_start": 0, "char_end": 1200}, ...]
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    if len(text) <= chunk_size:
        return [{"index": 0, "text": text, "char_start": 0, "char_end": len(text)}]

    chunks = []
    start = 0
    index = 0

    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            # Try to break at paragraph boundary
            para_break = text.rfind('\n\n', start, end)
            if para_break > start + chunk_size // 2:
                end = para_break + 2
            else:
                # Try sentence boundary (period followed by space or newline)
                sentence_break = text.rfind('. ', start + chunk_size // 2, end)
                if sentence_break > start:
                    end = sentence_break + 2
                else:
                    # Try newline boundary
                    line_break = text.rfind('\n', start + chunk_size // 2, end)
                    if line_break > start:
                        end = line_break + 1

        chunk_text_content = text[start:end].strip()
        if chunk_text_content:
            chunks.append({
                "index": index,
                "text": chunk_text_content,
                "char_start": start,
                "char_end": end,
            })
            index += 1

        start = end - chunk_overlap
        if start >= len(text):
            break

    return chunks


def estimate_token_count(text: str) -> int:
    """Rough token count estimate. Spanish averages ~3 chars/token."""
    return len(text) // 3
