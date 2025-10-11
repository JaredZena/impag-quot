-- Fix missing trigger for product default_margin updates
-- This fixes the 500 error when updating default_margin on products

-- The function already exists, we just need to create the missing trigger
CREATE TRIGGER trigger_update_calculated_price_on_product
    BEFORE UPDATE OF default_margin
    ON product
    FOR EACH ROW
    EXECUTE FUNCTION update_calculated_price_on_product();

-- Verify the trigger was created
SELECT 
    trigger_name,
    event_manipulation,
    event_object_table,
    action_statement
FROM information_schema.triggers 
WHERE trigger_name = 'trigger_update_calculated_price_on_product';



