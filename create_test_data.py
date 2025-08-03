#!/usr/bin/env python3
"""
Simple script to create test data for supplier-product relationships
"""
import sys
from models import SessionLocal, Supplier, Product, SupplierProduct, ProductCategory, ProductUnit

def create_test_data():
    """Create some test data for suppliers, products, and relationships"""
    session = SessionLocal()
    try:
        # Check if we already have test data
        existing_supplier = session.query(Supplier).filter(Supplier.name == "Proveedor Test").first()
        if existing_supplier:
            print("Test data already exists, skipping creation")
            return

        # Create test category if it doesn't exist
        test_category = session.query(ProductCategory).filter(ProductCategory.name == "Categoria Test").first()
        if not test_category:
            test_category = ProductCategory(
                name="Categoria Test",
                slug="categoria-test"
            )
            session.add(test_category)
            session.flush()  # Get the ID

        # Create test supplier
        test_supplier = Supplier(
            name="Proveedor Test",
            common_name="Proveedor de Prueba",
            rfc="RFC123456789",
            description="Proveedor de prueba para testing",
            contact_name="Juan Perez",
            email="test@proveedor.com",
            phone="+52 55 1234 5678"
        )
        session.add(test_supplier)
        session.flush()  # Get the ID

        # Create test product
        test_product = Product(
            name="Producto Test",
            description="Producto de prueba para testing",
            base_sku="TEST-BASE",
            sku="TEST-001",
            category_id=test_category.id,
            unit=ProductUnit.PIEZA,
            price=150.00,
            stock=100,
            iva=True,
            is_active=True,
            specifications={"color": "azul", "material": "plastico"}
        )
        session.add(test_product)
        session.flush()  # Get the ID

        # Create supplier-product relationship
        supplier_product = SupplierProduct(
            supplier_id=test_supplier.id,
            product_id=test_product.id,
            supplier_sku="PROV-TEST-001",
            cost=120.00,
            stock=50,
            lead_time_days=7,
            is_active=True,
            notes="Relación de prueba"
        )
        session.add(supplier_product)

        # Create another test product for the same supplier
        test_product2 = Product(
            name="Producto Test 2",
            description="Segundo producto de prueba",
            base_sku="TEST2-BASE",
            sku="TEST-002",
            category_id=test_category.id,
            unit=ProductUnit.KG,
            price=75.50,
            stock=25,
            iva=True,
            is_active=True,
            specifications={"peso": "2kg", "tipo": "organico"}
        )
        session.add(test_product2)
        session.flush()

        supplier_product2 = SupplierProduct(
            supplier_id=test_supplier.id,
            product_id=test_product2.id,
            supplier_sku="PROV-TEST-002",
            cost=60.25,
            stock=30,
            lead_time_days=14,
            is_active=True,
            notes="Segunda relación de prueba"
        )
        session.add(supplier_product2)

        # Commit all changes
        session.commit()
        
        print("✅ Test data created successfully!")
        print(f"   - Created supplier: {test_supplier.name} (ID: {test_supplier.id})")
        print(f"   - Created product 1: {test_product.name} (ID: {test_product.id})")
        print(f"   - Created product 2: {test_product2.name} (ID: {test_product2.id})")
        print(f"   - Created 2 supplier-product relationships")
        print(f"\nYou can now test:")
        print(f"   - Product detail page: /product-admin/{test_product.id}")
        print(f"   - Supplier detail page: /supplier-admin/{test_supplier.id}")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error creating test data: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    create_test_data() 