-- Fix column name mismatch in trigger functions
-- The triggers were referencing 'shipping_cost_per_unit' but the actual column is 'shipping_cost_direct'

-- Update the trigger function for supplier_product changes
CREATE OR REPLACE FUNCTION update_calculated_price()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE product 
    SET 
        calculated_price = CASE 
            WHEN default_margin IS NOT NULL THEN
                ROUND(
                    (SELECT MIN(cost + COALESCE(shipping_cost_direct, 0)) 
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

-- Update the trigger function for product default_margin changes
CREATE OR REPLACE FUNCTION update_calculated_price_on_product()
RETURNS TRIGGER AS $$
BEGIN
    -- Only update if default_margin changed
    IF OLD.default_margin IS DISTINCT FROM NEW.default_margin THEN
        NEW.calculated_price = CASE 
            WHEN NEW.default_margin IS NOT NULL THEN
                ROUND(
                    (SELECT MIN(cost + COALESCE(shipping_cost_direct, 0)) 
                     FROM supplier_product 
                     WHERE product_id = NEW.id 
                       AND is_active = true 
                       AND cost IS NOT NULL 
                       AND cost > 0
                    ) / (1 - NEW.default_margin), 2
                )
            ELSE NULL
        END;
        NEW.calculated_price_updated_at = NOW();
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Update the supplier_product trigger to reference the correct column
DROP TRIGGER IF EXISTS trigger_update_calculated_price_on_supplier_product ON supplier_product;
CREATE TRIGGER trigger_update_calculated_price_on_supplier_product
    AFTER INSERT OR UPDATE OF cost, shipping_cost_direct, is_active
    ON supplier_product
    FOR EACH ROW
    EXECUTE FUNCTION update_calculated_price();

-- Verify the functions were updated
SELECT 
    routine_name,
    routine_definition
FROM information_schema.routines 
WHERE routine_name IN ('update_calculated_price', 'update_calculated_price_on_product')
AND routine_type = 'FUNCTION';



