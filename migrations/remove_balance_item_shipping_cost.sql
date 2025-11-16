-- Remove shipping_cost column from balance_item table
-- Shipping costs are now calculated dynamically from supplier_product

-- Remove the shipping_cost column since it's calculated dynamically
ALTER TABLE balance_item 
DROP COLUMN IF EXISTS shipping_cost;

-- Add comment to clarify the change
COMMENT ON TABLE balance_item IS 'Balance items - shipping costs calculated dynamically from supplier_product table';

