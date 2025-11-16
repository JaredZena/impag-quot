-- ============================================================================
-- Stock Backup Table Creation Script
-- ============================================================================
-- Purpose: Create a temporary backup table to preserve current stock state
--          before migrating to SupplierProduct as source of truth.
--
-- Safety: This table preserves the current state so we can:
--         1. Verify stock integrity after migration
--         2. Rollback if needed
--         3. Compare before/after totals
--
-- Usage: Run this BEFORE any stock migration operations
-- ============================================================================

-- Drop table if it exists (for re-running the script)
DROP TABLE IF EXISTS stock_backup CASCADE;

-- Create simple backup table - snapshot of Product table (what production app reads from)
CREATE TABLE stock_backup AS
SELECT 
    id,
    name,
    sku,
    stock,
    price,
    category_id,
    unit,
    package_size,
    iva,
    base_sku,
    specifications,
    default_margin,
    calculated_price,
    calculated_price_updated_at,
    is_active,
    archived_at,
    created_at,
    last_updated,
    CURRENT_TIMESTAMP as backup_created_at
FROM product
WHERE archived_at IS NULL;  -- Backup all active products

-- Add indexes for quick lookups
CREATE INDEX idx_stock_backup_id ON stock_backup(id);
CREATE INDEX idx_stock_backup_sku ON stock_backup(sku);
CREATE INDEX idx_stock_backup_stock ON stock_backup(stock) WHERE stock > 0;

-- Add comments for documentation
COMMENT ON TABLE stock_backup IS 'Temporary backup of Product table before migration - preserves current stock state that production app reads from';
COMMENT ON COLUMN stock_backup.backup_created_at IS 'Timestamp when this backup was created';

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Query 1: Summary of stock backup
SELECT 
    'Stock Backup Summary' as report_type,
    COUNT(*) as total_products_backed_up,
    COUNT(*) FILTER (WHERE stock > 0) as products_with_stock,
    COALESCE(SUM(stock), 0) as total_stock,
    COALESCE(AVG(stock), 0) as avg_stock,
    COALESCE(MAX(stock), 0) as max_stock
FROM stock_backup;

-- Query 2: Products with stock
SELECT 
    'Products with stock' as report_type,
    COUNT(*) as count,
    SUM(stock) as total_stock,
    AVG(stock) as avg_stock,
    MAX(stock) as max_stock
FROM stock_backup
WHERE stock > 0;

-- Query 3: Current state comparison (for verification after migration)
-- Run this AFTER migration to compare with backup
/*
SELECT 
    'Current State vs Backup' as comparison,
    (SELECT SUM(stock) FROM product WHERE archived_at IS NULL) as current_product_stock,
    (SELECT SUM(stock) FROM supplier_product WHERE archived_at IS NULL) as current_supplier_product_stock,
    (SELECT SUM(stock) FROM stock_backup) as backup_stock;
*/

-- ============================================================================
-- Success Message
-- ============================================================================

DO $$
DECLARE
    product_count INTEGER;
    products_with_stock INTEGER;
    stock_total INTEGER;
BEGIN
    SELECT COUNT(*) INTO product_count FROM stock_backup;
    SELECT COUNT(*) INTO products_with_stock FROM stock_backup WHERE stock > 0;
    SELECT COALESCE(SUM(stock), 0) INTO stock_total FROM stock_backup;
    
    RAISE NOTICE '✅ Stock backup table created successfully!';
    RAISE NOTICE '📊 Backed up % products (% with stock > 0)', product_count, products_with_stock;
    RAISE NOTICE '📦 Total stock backed up: % units', stock_total;
    RAISE NOTICE '';
    RAISE NOTICE '💡 Next steps:';
    RAISE NOTICE '   1. Review the verification queries above';
    RAISE NOTICE '   2. Run populate_supplier_product_stock.py to migrate stock';
    RAISE NOTICE '   3. Verify stock integrity with verify_stock_integrity.sql';
END $$;

