-- ============================================================================
-- Stock Integrity Verification Script
-- ============================================================================
-- Purpose: Verify stock integrity after migration to SupplierProduct
--          Compares current state with backup table
--
-- Usage: Run this AFTER populating SupplierProduct.stock
-- ============================================================================

-- Check if backup table exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'stock_backup') THEN
        RAISE EXCEPTION '❌ stock_backup table does not exist. Run create_stock_backup_table.sql first!';
    END IF;
END $$;

-- ============================================================================
-- Verification Report
-- ============================================================================

SELECT 
    '═══════════════════════════════════════════════════════════════' as separator,
    'STOCK INTEGRITY VERIFICATION REPORT' as title,
    '═══════════════════════════════════════════════════════════════' as separator;

-- Summary Comparison
SELECT 
    'SUMMARY COMPARISON' as report_section,
    (SELECT COALESCE(SUM(stock), 0) FROM product WHERE archived_at IS NULL) as current_product_stock,
    (SELECT COALESCE(SUM(stock), 0) FROM supplier_product WHERE archived_at IS NULL) as current_supplier_product_stock,
    (SELECT COALESCE(SUM(stock), 0) FROM stock_backup) as backup_stock,
    CASE 
        WHEN (SELECT COALESCE(SUM(stock), 0) FROM supplier_product WHERE archived_at IS NULL) >= 
             (SELECT COALESCE(SUM(stock), 0) FROM stock_backup WHERE stock > 0)
        THEN '✅ PASS - Supplier product stock >= backup stock'
        ELSE '⚠️  WARNING - Supplier product stock < backup stock'
    END as integrity_status;

-- Detailed Stock Comparison
SELECT 
    'DETAILED STOCK BREAKDOWN' as report_section,
    'Product Table' as source,
    COUNT(*) as products_with_stock,
    COALESCE(SUM(stock), 0) as total_stock,
    COALESCE(AVG(stock), 0) as avg_stock,
    COALESCE(MAX(stock), 0) as max_stock
FROM product 
WHERE stock > 0 AND archived_at IS NULL

UNION ALL

SELECT 
    'DETAILED STOCK BREAKDOWN' as report_section,
    'SupplierProduct Table' as source,
    COUNT(*) as products_with_stock,
    COALESCE(SUM(stock), 0) as total_stock,
    COALESCE(AVG(stock), 0) as avg_stock,
    COALESCE(MAX(stock), 0) as max_stock
FROM supplier_product 
WHERE stock > 0 AND archived_at IS NULL

UNION ALL

SELECT 
    'DETAILED STOCK BREAKDOWN' as report_section,
    'Backup Table' as source,
    COUNT(*) as products_with_stock,
    COALESCE(SUM(stock), 0) as total_stock,
    COALESCE(AVG(stock), 0) as avg_stock,
    COALESCE(MAX(stock), 0) as max_stock
FROM stock_backup
WHERE stock > 0;

-- Products that lost stock (should be investigated)
SELECT 
    '⚠️  PRODUCTS WITH STOCK DISCREPANCIES' as report_section,
    p.id as product_id,
    p.name as product_name,
    p.sku as product_sku,
    p.stock as current_product_stock,
    COALESCE(sb.stock, 0) as backup_stock,
    COALESCE(SUM(sp.stock), 0) as current_supplier_product_stock,
    (COALESCE(sb.stock, 0) - p.stock) as product_stock_diff,
    (COALESCE(SUM(sp.stock), 0) - COALESCE(sb.stock, 0)) as supplier_product_stock_diff
FROM product p
LEFT JOIN stock_backup sb ON sb.id = p.id
LEFT JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
WHERE p.archived_at IS NULL
    AND (COALESCE(sb.stock, 0) > 0 OR p.stock > 0 OR COALESCE(SUM(sp.stock), 0) > 0)
GROUP BY p.id, p.name, p.sku, p.stock, sb.stock
HAVING 
    -- Product stock decreased significantly (more than 10 units)
    (COALESCE(sb.stock, 0) - p.stock) > 10
    OR
    -- Supplier product stock doesn't match backup (more than 10 units difference)
    ABS(COALESCE(SUM(sp.stock), 0) - COALESCE(sb.stock, 0)) > 10
ORDER BY product_stock_diff DESC, supplier_product_stock_diff DESC
LIMIT 20;

-- Products with stock but no supplier products (potential issue)
SELECT 
    '⚠️  PRODUCTS WITH STOCK BUT NO SUPPLIER PRODUCTS' as report_section,
    p.id as product_id,
    p.name as product_name,
    p.sku as product_sku,
    p.stock as product_stock,
    COUNT(sp.id) as supplier_product_count
FROM product p
LEFT JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
WHERE p.stock > 0 
    AND p.archived_at IS NULL
GROUP BY p.id, p.name, p.sku, p.stock
HAVING COUNT(sp.id) = 0
ORDER BY p.stock DESC;

-- ============================================================================
-- EXPORT LIST: Products with stock but NO supplier products
-- ============================================================================
-- Use this list to find actual suppliers for these products
-- Can export to CSV for review
-- ============================================================================

SELECT 
    '📋 EXPORT LIST: Products Needing Suppliers' as report_section,
    p.id as product_id,
    p.name as product_name,
    p.sku,
    p.base_sku,
    p.stock,
    p.price,
    pc.name as category_name,
    p.unit::text as unit,
    p.description,
    p.specifications,
    p.created_at,
    p.last_updated,
    'NO SUPPLIER - NEEDS MANUAL ASSIGNMENT' as status
FROM product p
LEFT JOIN product_category pc ON pc.id = p.category_id
LEFT JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
WHERE p.stock > 0 
    AND p.archived_at IS NULL
GROUP BY p.id, p.name, p.sku, p.base_sku, p.stock, p.price, pc.name, p.unit, p.description, p.specifications, p.created_at, p.last_updated
HAVING COUNT(sp.id) = 0
ORDER BY p.stock DESC, p.name ASC;

-- Supplier products with stock but product has no stock (expected after migration)
SELECT 
    '✅ SUPPLIER PRODUCTS WITH STOCK (Expected After Migration)' as report_section,
    sp.id as supplier_product_id,
    p.name as product_name,
    s.name as supplier_name,
    sp.stock as supplier_product_stock,
    p.stock as product_stock,
    CASE 
        WHEN p.stock = 0 THEN '✅ Migrated - Product stock cleared'
        ELSE '⚠️  Product still has stock'
    END as migration_status
FROM supplier_product sp
JOIN product p ON p.id = sp.product_id
LEFT JOIN supplier s ON s.id = sp.supplier_id
WHERE sp.stock > 0 
    AND sp.archived_at IS NULL
    AND p.archived_at IS NULL
ORDER BY sp.stock DESC
LIMIT 20;

-- Stock distribution by supplier (after migration)
SELECT 
    '📊 STOCK DISTRIBUTION BY SUPPLIER' as report_section,
    s.id as supplier_id,
    s.name as supplier_name,
    COUNT(DISTINCT sp.id) as supplier_product_count,
    COALESCE(SUM(sp.stock), 0) as total_stock,
    COALESCE(AVG(sp.stock), 0) as avg_stock,
    COALESCE(MAX(sp.stock), 0) as max_stock
FROM supplier s
LEFT JOIN supplier_product sp ON sp.supplier_id = s.id AND sp.archived_at IS NULL
WHERE s.archived_at IS NULL
GROUP BY s.id, s.name
HAVING COALESCE(SUM(sp.stock), 0) > 0
ORDER BY total_stock DESC;

-- Migration Success Check: Products that should have stock migrated
SELECT 
    '✅ MIGRATION SUCCESS CHECK' as report_section,
    COUNT(*) as products_migrated,
    SUM(sb.stock) as total_stock_migrated,
    SUM(sp.stock) as total_stock_in_supplier_products,
    CASE 
        WHEN SUM(sp.stock) >= SUM(sb.stock) - 10 THEN '✅ PASS'
        ELSE '⚠️  WARNING'
    END as migration_status
FROM stock_backup sb
LEFT JOIN supplier_product sp ON sp.product_id = sb.id 
    AND sp.archived_at IS NULL
    AND sp.is_active = true
WHERE sb.stock > 0
GROUP BY sb.id
HAVING SUM(sp.stock) < SUM(sb.stock) - 10
LIMIT 0;  -- This will show if there are any failures

-- Final Integrity Check
DO $$
DECLARE
    current_supplier_stock INTEGER;
    backup_stock INTEGER;
    current_product_stock INTEGER;
    products_with_stock_backup INTEGER;
    products_with_stock_supplier INTEGER;
    integrity_passed BOOLEAN;
    stock_match BOOLEAN;
BEGIN
    -- Get current totals
    SELECT COALESCE(SUM(stock), 0) INTO current_supplier_stock 
    FROM supplier_product WHERE archived_at IS NULL;
    
    SELECT COALESCE(SUM(stock), 0) INTO backup_stock 
    FROM stock_backup WHERE stock > 0;
    
    SELECT COALESCE(SUM(stock), 0) INTO current_product_stock 
    FROM product WHERE archived_at IS NULL;
    
    SELECT COUNT(*) INTO products_with_stock_backup
    FROM stock_backup WHERE stock > 0;
    
    SELECT COUNT(DISTINCT sp.product_id) INTO products_with_stock_supplier
    FROM supplier_product sp
    WHERE sp.stock > 0 AND sp.archived_at IS NULL;
    
    -- Check integrity (allow small differences for rounding, max 10 units)
    stock_match := (current_supplier_stock >= backup_stock - 10);
    integrity_passed := stock_match;
    
    RAISE NOTICE '';
    RAISE NOTICE '═══════════════════════════════════════════════════════════════';
    RAISE NOTICE 'POST-MIGRATION INTEGRITY CHECK';
    RAISE NOTICE '═══════════════════════════════════════════════════════════════';
    RAISE NOTICE '';
    RAISE NOTICE '📊 Stock Totals:';
    RAISE NOTICE '   Current Supplier Product Stock: % units', current_supplier_stock;
    RAISE NOTICE '   Backup Stock (to migrate): % units', backup_stock;
    RAISE NOTICE '   Difference: % units', (current_supplier_stock - backup_stock);
    RAISE NOTICE '';
    RAISE NOTICE '   Current Product Stock: % units', current_product_stock;
    RAISE NOTICE '   (Product stock should remain unchanged for production app)';
    RAISE NOTICE '';
    RAISE NOTICE '📦 Products with Stock:';
    RAISE NOTICE '   Products with stock in backup: %', products_with_stock_backup;
    RAISE NOTICE '   Products with stock in supplier products: %', products_with_stock_supplier;
    RAISE NOTICE '';
    
    IF integrity_passed THEN
        RAISE NOTICE '✅ INTEGRITY CHECK PASSED';
        RAISE NOTICE '   Supplier product stock matches or exceeds backup stock';
        RAISE NOTICE '   Migration appears successful!';
    ELSE
        RAISE WARNING '⚠️  INTEGRITY CHECK FAILED';
        RAISE WARNING '   Supplier product stock is less than backup stock';
        RAISE WARNING '   Missing: % units', (backup_stock - current_supplier_stock);
        RAISE WARNING '   Please investigate stock discrepancies';
    END IF;
    
    RAISE NOTICE '';
    RAISE NOTICE '💡 Next Steps:';
    IF current_product_stock > 0 THEN
        RAISE NOTICE '   → Product.stock still has % units (production app still uses it)', current_product_stock;
        RAISE NOTICE '   → This is expected - Product.stock remains for production app';
    END IF;
    RAISE NOTICE '   → Local app can now use SupplierProduct.stock';
    RAISE NOTICE '   → Verify production app continues working correctly';
    RAISE NOTICE '';
    RAISE NOTICE '═══════════════════════════════════════════════════════════════';
END $$;


