-- Migration to drop price and stock columns from product table
-- Run this AFTER the stock migration script has been executed and verified

-- Drop the 'price' column from the 'product' table
ALTER TABLE product DROP COLUMN price;

-- Drop the 'stock' column from the 'product' table  
ALTER TABLE product DROP COLUMN stock;

-- Note: calculated_price and calculated_price_updated_at columns are kept
-- for performance caching of calculated prices