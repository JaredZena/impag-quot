-- ============================================================================
-- PRE-MIGRATION VERIFICATION SCRIPT
-- ============================================================================
-- Purpose: Verify database state BEFORE running stock migration
--          Identifies potential issues that need to be addressed
--
-- Usage: Run this BEFORE populate_supplier_product_stock.py
-- ============================================================================

-- Check if backup table exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'stock_backup') THEN
        RAISE WARNING '⚠️  stock_backup table does not exist. Run create_stock_backup_table.sql first!';
    END IF;
END $$;

-- ============================================================================
-- CRITICAL CHECKS (Must Address Before Migration)
-- ============================================================================

-- 1. CRITICAL: Products with stock but NO supplier products
-- These will need a supplier product created or manual handling
SELECT 
    '⚠️  CRITICAL: Products with stock but NO supplier products' as check_type,
    p.id as product_id,
    p.name as product_name,
    p.sku,
    p.stock,
    COUNT(sp.id) as supplier_product_count
FROM product p
LEFT JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
WHERE p.stock > 0 
    AND p.archived_at IS NULL
GROUP BY p.id, p.name, p.sku, p.stock
HAVING COUNT(sp.id) = 0
ORDER BY p.stock DESC;

-- ============================================================================
-- EXPORT LIST: Products with stock but NO supplier products (Detailed)
-- ============================================================================
-- Use this list to find actual suppliers for these products BEFORE migration
-- Can export to CSV for review
-- ============================================================================

SELECT 
    '📋 EXPORT LIST: Products Needing Suppliers (Before Migration)' as check_type,
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
    'NO SUPPLIER - WILL BE SKIPPED IN MIGRATION' as status
FROM product p
LEFT JOIN product_category pc ON pc.id = p.category_id
LEFT JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
WHERE p.stock > 0 
    AND p.archived_at IS NULL
GROUP BY p.id, p.name, p.sku, p.base_sku, p.stock, p.price, pc.name, p.unit, p.description, p.specifications, p.created_at, p.last_updated
HAVING COUNT(sp.id) = 0
ORDER BY p.stock DESC, p.name ASC;

-- 2. Products with stock that have MULTIPLE supplier products
-- Need to decide which supplier gets the stock (cheapest? first? manual?)
SELECT 
    '⚠️  Products with stock and MULTIPLE supplier products' as check_type,
    p.id as product_id,
    p.name as product_name,
    p.sku,
    p.stock,
    COUNT(sp.id) as supplier_product_count,
    STRING_AGG(s.name || ' (ID: ' || sp.id || ', Cost: ' || COALESCE(sp.cost::text, 'NULL') || ')', ', ') as suppliers
FROM product p
JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
LEFT JOIN supplier s ON s.id = sp.supplier_id
WHERE p.stock > 0 
    AND p.archived_at IS NULL
GROUP BY p.id, p.name, p.sku, p.stock
HAVING COUNT(sp.id) > 1
ORDER BY p.stock DESC;

-- ============================================================================
-- WARNINGS (Review Before Migration)
-- ============================================================================

-- 3. Supplier products that already have stock (will be ADDED to, not replaced)
SELECT 
    '⚠️  Supplier products that ALREADY have stock' as check_type,
    sp.id as supplier_product_id,
    p.id as product_id,
    p.name as product_name,
    p.stock as product_stock,
    sp.stock as current_supplier_product_stock,
    s.name as supplier_name,
    (p.stock + sp.stock) as total_after_migration
FROM supplier_product sp
JOIN product p ON p.id = sp.product_id
LEFT JOIN supplier s ON s.id = sp.supplier_id
WHERE p.stock > 0 
    AND sp.stock > 0
    AND p.archived_at IS NULL
    AND sp.archived_at IS NULL
ORDER BY p.stock DESC
LIMIT 20;

-- 4. Data integrity: Orphaned supplier products (product_id doesn't exist)
SELECT 
    '⚠️  Orphaned supplier products (product_id invalid)' as check_type,
    COUNT(*) as count,
    STRING_AGG(sp.id::text, ', ') as supplier_product_ids
FROM supplier_product sp
LEFT JOIN product p ON p.id = sp.product_id
WHERE sp.product_id IS NOT NULL 
    AND p.id IS NULL
    AND sp.archived_at IS NULL;

-- 5. Data integrity: Supplier products with NULL product_id
SELECT 
    '⚠️  Supplier products with NULL product_id' as check_type,
    COUNT(*) as count
FROM supplier_product
WHERE product_id IS NULL
    AND archived_at IS NULL;

-- ============================================================================
-- SUMMARY & STATISTICS
-- ============================================================================

-- 6. Current stock distribution summary
SELECT 
    '📊 Current Stock Distribution' as check_type,
    (SELECT COUNT(*) FROM product WHERE stock > 0 AND archived_at IS NULL) as products_with_stock,
    (SELECT COALESCE(SUM(stock), 0) FROM product WHERE archived_at IS NULL) as total_product_stock,
    (SELECT COUNT(*) FROM supplier_product WHERE stock > 0 AND archived_at IS NULL) as supplier_products_with_stock,
    (SELECT COALESCE(SUM(stock), 0) FROM supplier_product WHERE archived_at IS NULL) as total_supplier_product_stock;

-- 7. Migration impact preview
SELECT 
    '📊 Migration Impact Preview' as check_type,
    (SELECT COUNT(*) FROM product p 
     WHERE p.stock > 0 
     AND p.archived_at IS NULL 
     AND NOT EXISTS (
         SELECT 1 FROM supplier_product sp 
         WHERE sp.product_id = p.id AND sp.archived_at IS NULL
     )) as products_needing_supplier_product_creation,
    (SELECT COUNT(*) FROM product p
     WHERE p.stock > 0
     AND p.archived_at IS NULL
     AND (SELECT COUNT(*) FROM supplier_product sp 
          WHERE sp.product_id = p.id AND sp.archived_at IS NULL) > 1) as products_with_multiple_suppliers,
    (SELECT COUNT(*) FROM supplier_product sp
     JOIN product p ON p.id = sp.product_id
     WHERE p.stock > 0
     AND sp.stock > 0
     AND p.archived_at IS NULL
     AND sp.archived_at IS NULL) as supplier_products_that_will_have_stock_added;

-- 8. Final verification: Total stock that will be migrated
SELECT 
    '✅ Total Stock to Migrate' as check_type,
    (SELECT COALESCE(SUM(stock), 0) FROM product WHERE archived_at IS NULL) as current_product_stock,
    (SELECT COALESCE(SUM(stock), 0) FROM stock_backup) as backup_stock,
    CASE 
        WHEN (SELECT COALESCE(SUM(stock), 0) FROM product WHERE archived_at IS NULL) = 
             (SELECT COALESCE(SUM(stock), 0) FROM stock_backup)
        THEN '✅ MATCH - Ready to migrate'
        ELSE '⚠️  MISMATCH - Investigate before migrating'
    END as verification_status;

-- 9. Sample: Products with stock and their supplier products (detailed view)
SELECT 
    '📋 Sample: Products with stock and supplier products' as check_type,
    p.id as product_id,
    p.name as product_name,
    p.stock as product_stock,
    sp.id as supplier_product_id,
    s.name as supplier_name,
    sp.cost,
    sp.stock as supplier_product_stock,
    sp.is_active as supplier_product_active,
    CASE 
        WHEN sp.cost IS NOT NULL THEN sp.cost
        ELSE 999999
    END as sort_cost
FROM product p
JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
LEFT JOIN supplier s ON s.id = sp.supplier_id
WHERE p.stock > 0 
    AND p.archived_at IS NULL
ORDER BY p.id, sort_cost ASC
LIMIT 50;  -- Show first 50 for review

-- ============================================================================
-- Final Summary Report
-- ============================================================================

DO $$
DECLARE
    products_no_supplier INTEGER;
    products_multiple_suppliers INTEGER;
    supplier_products_with_existing_stock INTEGER;
    orphaned_count INTEGER;
    total_product_stock INTEGER;
    backup_stock INTEGER;
    ready_to_migrate BOOLEAN;
BEGIN
    -- Count issues
    SELECT COUNT(*) INTO products_no_supplier
    FROM product p
    WHERE p.stock > 0 
        AND p.archived_at IS NULL
        AND NOT EXISTS (
            SELECT 1 FROM supplier_product sp 
            WHERE sp.product_id = p.id AND sp.archived_at IS NULL
        );
    
    SELECT COUNT(*) INTO products_multiple_suppliers
    FROM product p
    WHERE p.stock > 0
        AND p.archived_at IS NULL
        AND (SELECT COUNT(*) FROM supplier_product sp 
             WHERE sp.product_id = p.id AND sp.archived_at IS NULL) > 1;
    
    SELECT COUNT(*) INTO supplier_products_with_existing_stock
    FROM supplier_product sp
    JOIN product p ON p.id = sp.product_id
    WHERE p.stock > 0
        AND sp.stock > 0
        AND p.archived_at IS NULL
        AND sp.archived_at IS NULL;
    
    SELECT COUNT(*) INTO orphaned_count
    FROM supplier_product sp
    LEFT JOIN product p ON p.id = sp.product_id
    WHERE sp.product_id IS NOT NULL 
        AND p.id IS NULL
        AND sp.archived_at IS NULL;
    
    SELECT COALESCE(SUM(stock), 0) INTO total_product_stock
    FROM product WHERE archived_at IS NULL;
    
    SELECT COALESCE(SUM(stock), 0) INTO backup_stock
    FROM stock_backup;
    
    ready_to_migrate := (products_no_supplier = 0) AND (orphaned_count = 0) AND (total_product_stock = backup_stock);
    
    RAISE NOTICE '';
    RAISE NOTICE '═══════════════════════════════════════════════════════════════';
    RAISE NOTICE 'PRE-MIGRATION VERIFICATION SUMMARY';
    RAISE NOTICE '═══════════════════════════════════════════════════════════════';
    RAISE NOTICE '';
    RAISE NOTICE '📊 Stock Totals:';
    RAISE NOTICE '   Current Product Stock: % units', total_product_stock;
    RAISE NOTICE '   Backup Stock: % units', backup_stock;
    RAISE NOTICE '   Match: %', CASE WHEN total_product_stock = backup_stock THEN '✅ YES' ELSE '❌ NO' END;
    RAISE NOTICE '';
    RAISE NOTICE '⚠️  Issues Found:';
    RAISE NOTICE '   Products with stock but NO supplier products: %', products_no_supplier;
    RAISE NOTICE '   Products with MULTIPLE supplier products: %', products_multiple_suppliers;
    RAISE NOTICE '   Supplier products that already have stock: %', supplier_products_with_existing_stock;
    RAISE NOTICE '   Orphaned supplier products: %', orphaned_count;
    RAISE NOTICE '';
    
    IF ready_to_migrate THEN
        RAISE NOTICE '✅ READY TO MIGRATE';
        RAISE NOTICE '   All critical checks passed. You can proceed with migration.';
    ELSE
        RAISE WARNING '⚠️  NOT READY TO MIGRATE';
        RAISE WARNING '   Please address the issues above before proceeding.';
        IF products_no_supplier > 0 THEN
            RAISE WARNING '   → Create supplier products for products with stock';
        END IF;
        IF orphaned_count > 0 THEN
            RAISE WARNING '   → Fix orphaned supplier products';
        END IF;
        IF total_product_stock != backup_stock THEN
            RAISE WARNING '   → Investigate stock mismatch';
        END IF;
    END IF;
    
    RAISE NOTICE '';
    RAISE NOTICE '═══════════════════════════════════════════════════════════════';
END $$;

