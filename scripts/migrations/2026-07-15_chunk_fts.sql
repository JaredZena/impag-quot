-- Hybrid search: Spanish full-text index over chunk text.
-- Generated column keeps the tsvector in sync automatically; GIN makes it fast.
ALTER TABLE document_chunk
  ADD COLUMN IF NOT EXISTS chunk_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(chunk_text, ''))) STORED;

CREATE INDEX IF NOT EXISTS ix_document_chunk_tsv
  ON document_chunk USING gin (chunk_tsv);

-- Filename lookups (exact-SKU / folio queries) use trigram similarity.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS ix_file_metadata_filename_trgm
  ON file_metadata USING gin (original_filename gin_trgm_ops);
