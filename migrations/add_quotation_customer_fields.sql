-- ============================================================================
-- Add Customer Fields to Quotation Table
-- ============================================================================
-- Purpose: Add customer_name, customer_location, and quotation_id columns
--          to the quotation table for storing customer information
--
-- Date: 2025-11-21
-- ============================================================================

-- Add customer_name column
ALTER TABLE quotation 
ADD COLUMN IF NOT EXISTS customer_name VARCHAR(200);

-- Add customer_location column
ALTER TABLE quotation 
ADD COLUMN IF NOT EXISTS customer_location VARCHAR(200);

-- Add quotation_id column
ALTER TABLE quotation 
ADD COLUMN IF NOT EXISTS quotation_id VARCHAR(50);

-- Add comments for documentation
COMMENT ON COLUMN quotation.customer_name IS 'Name of the customer for the quotation';
COMMENT ON COLUMN quotation.customer_location IS 'Location/address of the customer';
COMMENT ON COLUMN quotation.quotation_id IS 'Generated quotation ID for reference';

-- ============================================================================
-- Verification
-- ============================================================================

-- Verify columns were added
SELECT 
    column_name, 
    data_type, 
    character_maximum_length,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'quotation' 
  AND column_name IN ('customer_name', 'customer_location', 'quotation_id');


