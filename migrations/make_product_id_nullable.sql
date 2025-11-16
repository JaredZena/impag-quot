-- Make product_id column nullable in supplier_product table
-- This allows SupplierProduct to be standalone (not linked to Product table)

-- Remove NOT NULL constraint from product_id
ALTER TABLE supplier_product 
ALTER COLUMN product_id DROP NOT NULL;

-- Verify the change
SELECT column_name, is_nullable, data_type 
FROM information_schema.columns 
WHERE table_name = 'supplier_product' 
  AND column_name = 'product_id';

