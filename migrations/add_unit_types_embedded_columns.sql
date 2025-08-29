-- Migration script to add new unit types and embedded columns
-- Run this after updating the models.py file

-- 1. Add new unit types to the ProductUnit enum
-- First, check if the new values already exist, then add them if they don't
DO $$ 
BEGIN
    -- Add PAQUETE to the enum if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'PAQUETE' AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'productunit')) THEN
        ALTER TYPE productunit ADD VALUE 'PAQUETE';
    END IF;
    
    -- Add KIT to the enum if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'KIT' AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'productunit')) THEN
        ALTER TYPE productunit ADD VALUE 'KIT';
    END IF;
END
$$;

-- 2. Add embedded column to product table
ALTER TABLE product 
ADD COLUMN IF NOT EXISTS embedded BOOLEAN DEFAULT FALSE NOT NULL;

-- 3. Add embedded column to supplier table
ALTER TABLE supplier 
ADD COLUMN IF NOT EXISTS embedded BOOLEAN DEFAULT FALSE NOT NULL;

-- 4. Add embedded column to supplier_product table
ALTER TABLE supplier_product 
ADD COLUMN IF NOT EXISTS embedded BOOLEAN DEFAULT FALSE NOT NULL;

-- 5. Update default_margin to 0.25 where it's NULL
UPDATE product 
SET default_margin = 0.25 
WHERE default_margin IS NULL;

-- 6. Create indexes for the new embedded columns for better performance
CREATE INDEX IF NOT EXISTS idx_product_embedded ON product(embedded);
CREATE INDEX IF NOT EXISTS idx_supplier_embedded ON supplier(embedded);
CREATE INDEX IF NOT EXISTS idx_supplier_product_embedded ON supplier_product(embedded);

-- 7. Add comments to document the new columns
COMMENT ON COLUMN product.embedded IS 'Whether this product has been embedded to embeddings database';
COMMENT ON COLUMN supplier.embedded IS 'Whether this supplier has been embedded to embeddings database';
COMMENT ON COLUMN supplier_product.embedded IS 'Whether this supplier product has been embedded to embeddings database';

-- 8. Show the results
SELECT 'Migration completed successfully' as status;

-- Check the enum values
SELECT enumlabel as unit_types 
FROM pg_enum 
WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'productunit')
ORDER BY enumsortorder;

-- Check products with default margin
SELECT 
    COUNT(*) as total_products,
    COUNT(default_margin) as products_with_margin,
    COUNT(*) - COUNT(default_margin) as products_without_margin
FROM product;

-- Check embedded columns
SELECT 
    'product' as table_name,
    COUNT(*) as total_rows,
    COUNT(CASE WHEN embedded = true THEN 1 END) as embedded_true,
    COUNT(CASE WHEN embedded = false THEN 1 END) as embedded_false
FROM product
UNION ALL
SELECT 
    'supplier' as table_name,
    COUNT(*) as total_rows,
    COUNT(CASE WHEN embedded = true THEN 1 END) as embedded_true,
    COUNT(CASE WHEN embedded = false THEN 1 END) as embedded_false
FROM supplier
UNION ALL
SELECT 
    'supplier_product' as table_name,
    COUNT(*) as total_rows,
    COUNT(CASE WHEN embedded = true THEN 1 END) as embedded_true,
    COUNT(CASE WHEN embedded = false THEN 1 END) as embedded_false
FROM supplier_product;
