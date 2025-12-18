-- Add indexes to optimize supplier_product queries in social calendar
-- These indexes will significantly improve performance for:
-- 1. Filtering active supplier products (is_active)
-- 2. Searching products by name with category filtering
-- 3. Joining products with categories
-- 4. Vector similarity search using embeddings

-- Index for filtering active supplier products (most common filter)
CREATE INDEX IF NOT EXISTS idx_supplier_product_is_active ON supplier_product(is_active) WHERE is_active = true AND archived_at IS NULL;

-- Composite index for common query pattern: active products by category
-- This optimizes queries like: WHERE is_active = true AND category_id = X AND archived_at IS NULL
CREATE INDEX IF NOT EXISTS idx_supplier_product_is_active_category ON supplier_product(is_active, category_id) WHERE is_active = true AND archived_at IS NULL;

-- Index for category_id (if not already indexed by foreign key)
-- Foreign keys sometimes don't automatically create indexes in all databases
CREATE INDEX IF NOT EXISTS idx_supplier_product_category_id ON supplier_product(category_id);

-- Composite index for name searches on active products
-- Note: This helps with prefix searches, but leading wildcards (%query%) still require full scan
-- For better performance with ILIKE, consider using full-text search or trigram indexes
CREATE INDEX IF NOT EXISTS idx_supplier_product_name_active ON supplier_product(name, is_active) WHERE is_active = true AND archived_at IS NULL;

-- Index for archived_at to optimize soft delete filtering
CREATE INDEX IF NOT EXISTS idx_supplier_product_archived_at ON supplier_product(archived_at) WHERE archived_at IS NULL;

-- Note: Embedding index already exists from add_embeddings_vector.sql migration
-- supplier_product_embedding_idx using ivfflat for vector similarity search

-- Add comments for documentation
COMMENT ON INDEX idx_supplier_product_is_active IS 'Index for filtering active supplier products - used in most product queries';
COMMENT ON INDEX idx_supplier_product_is_active_category IS 'Composite index for active supplier products by category - optimizes joins with ProductCategory';
COMMENT ON INDEX idx_supplier_product_category_id IS 'Index on category_id foreign key for faster category joins';
COMMENT ON INDEX idx_supplier_product_name_active IS 'Composite index for name searches on active supplier products';
COMMENT ON INDEX idx_supplier_product_archived_at IS 'Index for filtering non-archived supplier products (soft delete)';



