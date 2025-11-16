-- PostgreSQL Migration Script
-- Add new columns to existing tables for shipping costs and default margins

-- Add default_margin column to product table
-- Using NUMERIC(5,4) to store decimal values like 0.25 for 25%
ALTER TABLE product 
ADD COLUMN default_margin NUMERIC(5,4);

-- Add comment to explain the column
COMMENT ON COLUMN product.default_margin IS 'Default margin as decimal (0.25 = 25%)';

-- Add shipping_cost column to supplier_product table
-- Using NUMERIC(10,2) for currency values
ALTER TABLE supplier_product 
ADD COLUMN shipping_cost NUMERIC(10,2);

-- Add comment to explain the column
COMMENT ON COLUMN supplier_product.shipping_cost IS 'Shipping cost from this supplier in local currency';

-- Optional: Set default values for existing records
-- Uncomment these lines if you want to set default values for existing data

-- UPDATE product SET default_margin = 0.25 WHERE default_margin IS NULL;
-- UPDATE supplier_product SET shipping_cost = 0.00 WHERE shipping_cost IS NULL;

-- Verify the changes
SELECT 
    column_name, 
    data_type, 
    numeric_precision, 
    numeric_scale,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name IN ('product', 'supplier_product') 
  AND column_name IN ('default_margin', 'shipping_cost')
ORDER BY table_name, ordinal_position;
