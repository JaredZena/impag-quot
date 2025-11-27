-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Add embedding column to supplier_product table
ALTER TABLE supplier_product ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- Create index for faster similarity search
CREATE INDEX IF NOT EXISTS supplier_product_embedding_idx ON supplier_product USING ivfflat (embedding vector_cosine_ops);

-- Add embedding column to supplier table (since user added it to model)
ALTER TABLE supplier ADD COLUMN IF NOT EXISTS embedding vector(1536);
