-- Add shipping cost columns to supplier_product table
-- Since shipping costs vary by supplier, this is the logical place

-- Add shipping columns
ALTER TABLE supplier_product 
ADD COLUMN IF NOT EXISTS shipping_cost_per_unit NUMERIC(10, 2) DEFAULT 0.00;

ALTER TABLE supplier_product 
ADD COLUMN IF NOT EXISTS shipping_method VARCHAR(20) DEFAULT 'DIRECT';

-- Add check constraint for shipping method
ALTER TABLE supplier_product 
DROP CONSTRAINT IF EXISTS check_shipping_method;

ALTER TABLE supplier_product 
ADD CONSTRAINT check_shipping_method 
CHECK (shipping_method IN ('DIRECT', 'OCURRE'));

-- Add index for performance
CREATE INDEX IF NOT EXISTS idx_supplier_product_shipping_method ON supplier_product(shipping_method);

-- Add comments for clarity
COMMENT ON COLUMN supplier_product.shipping_cost_per_unit IS 'Costo de envío por unidad para este proveedor';
COMMENT ON COLUMN supplier_product.shipping_method IS 'Método de envío: DIRECT (directo a local) o OCURRE (vía Durango City)';

-- Update the existing trigger function to include shipping costs in calculation
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

-- Update trigger to fire on shipping cost changes too
DROP TRIGGER IF EXISTS trigger_update_calculated_price_on_supplier_product ON supplier_product;
CREATE TRIGGER trigger_update_calculated_price_on_supplier_product
    AFTER INSERT OR UPDATE OF cost, shipping_cost_direct, is_active
    ON supplier_product
    FOR EACH ROW
    EXECUTE FUNCTION update_calculated_price();

-- Update the product trigger function too
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

-- Create the missing trigger on product table for default_margin changes
DROP TRIGGER IF EXISTS trigger_update_calculated_price_on_product ON product;
CREATE TRIGGER trigger_update_calculated_price_on_product
    BEFORE UPDATE OF default_margin
    ON product
    FOR EACH ROW
    EXECUTE FUNCTION update_calculated_price_on_product();
