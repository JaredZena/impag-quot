-- Add calculated_price column to cache calculated prices for performance
-- This will dramatically improve API performance

-- 1. Add the calculated_price column
ALTER TABLE product 
ADD COLUMN IF NOT EXISTS calculated_price NUMERIC(10, 2) DEFAULT NULL;

-- 2. Add a timestamp to track when the calculation was last updated
ALTER TABLE product 
ADD COLUMN IF NOT EXISTS calculated_price_updated_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;

-- 3. Create an index for better performance
CREATE INDEX IF NOT EXISTS idx_product_calculated_price ON product(calculated_price);
CREATE INDEX IF NOT EXISTS idx_product_calculated_price_updated_at ON product(calculated_price_updated_at);

-- 4. Add a trigger function to automatically update calculated_price when relevant data changes
CREATE OR REPLACE FUNCTION update_calculated_price()
RETURNS TRIGGER AS $$
BEGIN
    -- This function will be called when supplier_product costs change
    -- or when product default_margin changes
    
    -- Update calculated_price for the affected product
    UPDATE product 
    SET 
        calculated_price = CASE 
            WHEN default_margin IS NOT NULL THEN
                ROUND(
                    (SELECT MIN(cost) 
                     FROM supplier_product 
                     WHERE product_id = NEW.product_id 
                       AND is_active = true 
                       AND cost IS NOT NULL 
                       AND cost > 0
                    ) / (1 - default_margin), 2
                )
            ELSE NULL
        END,
        calculated_price_updated_at = NOW()
    WHERE id = NEW.product_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 5. Create triggers to automatically update calculated prices
DROP TRIGGER IF EXISTS trigger_update_calculated_price_on_supplier_product ON supplier_product;
CREATE TRIGGER trigger_update_calculated_price_on_supplier_product
    AFTER INSERT OR UPDATE OF cost, is_active
    ON supplier_product
    FOR EACH ROW
    EXECUTE FUNCTION update_calculated_price();

-- 6. Initial population of calculated_price for existing products
UPDATE product 
SET 
    calculated_price = CASE 
        WHEN default_margin IS NOT NULL THEN
            ROUND(
                (SELECT MIN(sp.cost) 
                 FROM supplier_product sp 
                 WHERE sp.product_id = product.id 
                   AND sp.is_active = true 
                   AND sp.cost IS NOT NULL 
                   AND sp.cost > 0
                ) / (1 - default_margin), 2
            )
        ELSE NULL
    END,
    calculated_price_updated_at = NOW()
WHERE default_margin IS NOT NULL;

-- 7. Show results
SELECT 
    'Calculated prices updated' as status,
    COUNT(*) as total_products,
    COUNT(calculated_price) as products_with_calculated_price,
    COUNT(price) as products_with_set_price
FROM product;

-- 8. Show some examples
SELECT 
    name,
    price as set_price,
    calculated_price,
    default_margin,
    CASE 
        WHEN price IS NOT NULL THEN price
        WHEN calculated_price IS NOT NULL THEN calculated_price
        ELSE NULL
    END as display_price
FROM product 
WHERE calculated_price IS NOT NULL 
LIMIT 10;
