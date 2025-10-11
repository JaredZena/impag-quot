-- Add currency support for USD and MXN
-- This migration adds currency tracking to supplier_product table

-- Add currency column to supplier_product table
ALTER TABLE supplier_product 
ADD COLUMN currency VARCHAR(3) DEFAULT 'MXN' NOT NULL;

-- Add index for currency lookups
CREATE INDEX idx_supplier_product_currency ON supplier_product(currency);

-- Update existing records to have MXN currency (they're already in MXN)
UPDATE supplier_product SET currency = 'MXN' WHERE currency IS NULL;

-- Add check constraint to ensure only valid currencies
ALTER TABLE supplier_product 
ADD CONSTRAINT chk_supplier_product_currency 
CHECK (currency IN ('MXN', 'USD'));

-- Add comment to document the currency field
COMMENT ON COLUMN supplier_product.currency IS 'Currency of the cost and shipping costs (MXN or USD)';
