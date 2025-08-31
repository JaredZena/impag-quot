import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, get_db, Balance, BalanceItem, Product, Supplier, SupplierProduct, ProductCategory, ProductUnit
from main import app
import json

# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_balance.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="function")
def client():
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    # Create test client
    client = TestClient(app)
    
    yield client
    
    # Clean up
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def test_data(client):
    """Create test data for balance testing"""
    db = TestingSessionLocal()
    
    try:
        # Create test category
        category = ProductCategory(name="Test Category", slug="test-category")
        db.add(category)
        db.flush()
        
        # Create test suppliers
        supplier1 = Supplier(name="Test Supplier 1", email="supplier1@test.com")
        supplier2 = Supplier(name="Test Supplier 2", email="supplier2@test.com")
        db.add(supplier1)
        db.add(supplier2)
        db.flush()
        
        # Create test products
        product1 = Product(
            name="Test Product 1",
            description="Test Description 1",
            base_sku="TEST-1",
            sku="TEST-1-001",
            category_id=category.id,
            unit=ProductUnit.PIEZA,
            price=100.00,
            is_active=True
        )
        
        product2 = Product(
            name="Test Product 2",
            description="Test Description 2", 
            base_sku="TEST-2",
            sku="TEST-2-001",
            category_id=category.id,
            unit=ProductUnit.PIEZA,
            price=200.00,
            is_active=True
        )
        
        db.add(product1)
        db.add(product2)
        db.flush()
        
        # Create supplier-product relationships
        sp1 = SupplierProduct(
            supplier_id=supplier1.id,
            product_id=product1.id,
            cost=80.00,
            shipping_cost_direct=10.00,
            shipping_method='DIRECT',
            is_active=True
        )
        
        sp2 = SupplierProduct(
            supplier_id=supplier2.id,
            product_id=product1.id,
            cost=85.00,
            shipping_cost_direct=5.00,
            shipping_method='DIRECT',
            is_active=True
        )
        
        sp3 = SupplierProduct(
            supplier_id=supplier1.id,
            product_id=product2.id,
            cost=180.00,
            shipping_stage1_cost=10.00,
            shipping_stage2_cost=15.00,
            shipping_method='OCURRE',
            is_active=True
        )
        
        db.add(sp1)
        db.add(sp2)
        db.add(sp3)
        db.commit()
        
        return {
            "category_id": category.id,
            "supplier1_id": supplier1.id,
            "supplier2_id": supplier2.id,
            "product1_id": product1.id,
            "product2_id": product2.id
        }
        
    finally:
        db.close()

class TestBalanceAPI:
    
    def test_get_empty_balance_list(self, client):
        """Test getting empty balance list"""
        response = client.get("/balance")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_balance_success(self, client, test_data):
        """Test creating a balance successfully"""
        balance_data = {
            "name": "Solar Panel Quote Comparison",
            "description": "Comparing suppliers for solar project",
            "balance_type": "COMPARISON",
            "currency": "MXN",
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "supplier_id": test_data["supplier1_id"],
                    "quantity": 2,
                    "unit_price": 100.00,
                    "shipping_cost": 20.00,
                    "notes": "Primary supplier option"
                },
                {
                    "product_id": test_data["product1_id"],
                    "supplier_id": test_data["supplier2_id"],
                    "quantity": 2,
                    "unit_price": 95.00,
                    "shipping_cost": 25.00,
                    "notes": "Alternative supplier"
                }
            ]
        }
        
        response = client.post("/balance", json=balance_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == balance_data["name"]
        assert data["balance_type"] == balance_data["balance_type"]
        assert data["currency"] == balance_data["currency"]
        assert len(data["items"]) == 2
        assert data["total_amount"] == 480.00  # (100+20)*2 + (95+25)*2 = 240 + 240
        assert data["is_active"] is True

    def test_create_balance_invalid_product(self, client, test_data):
        """Test creating balance with invalid product fails"""
        balance_data = {
            "name": "Invalid Balance",
            "items": [
                {
                    "product_id": 99999,  # Non-existent product
                    "supplier_id": test_data["supplier1_id"],
                    "quantity": 1,
                    "unit_price": 100.00
                }
            ]
        }
        
        response = client.post("/balance", json=balance_data)
        assert response.status_code == 400
        assert "Product 99999 not found" in response.json()["detail"]

    def test_create_balance_invalid_supplier(self, client, test_data):
        """Test creating balance with invalid supplier fails"""
        balance_data = {
            "name": "Invalid Balance",
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "supplier_id": 99999,  # Non-existent supplier
                    "quantity": 1,
                    "unit_price": 100.00
                }
            ]
        }
        
        response = client.post("/balance", json=balance_data)
        assert response.status_code == 400
        assert "Supplier 99999 not found" in response.json()["detail"]

    def test_get_balance_by_id(self, client, test_data):
        """Test getting specific balance by ID"""
        # First create a balance
        balance_data = {
            "name": "Test Balance",
            "balance_type": "QUOTATION",
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "supplier_id": test_data["supplier1_id"],
                    "quantity": 1,
                    "unit_price": 100.00
                }
            ]
        }
        
        create_response = client.post("/balance", json=balance_data)
        balance_id = create_response.json()["id"]
        
        # Get the balance
        response = client.get(f"/balance/{balance_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["id"] == balance_id
        assert data["name"] == balance_data["name"]
        assert len(data["items"]) == 1

    def test_get_nonexistent_balance(self, client):
        """Test getting non-existent balance returns 404"""
        response = client.get("/balance/99999")
        assert response.status_code == 404
        assert "Balance not found" in response.json()["detail"]

    def test_update_balance(self, client, test_data):
        """Test updating a balance"""
        # Create balance first
        balance_data = {
            "name": "Original Balance",
            "balance_type": "QUOTATION",
            "items": []
        }
        
        create_response = client.post("/balance", json=balance_data)
        balance_id = create_response.json()["id"]
        
        # Update the balance
        update_data = {
            "name": "Updated Balance",
            "balance_type": "COMPARISON",
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "supplier_id": test_data["supplier1_id"],
                    "quantity": 1,
                    "unit_price": 150.00
                }
            ]
        }
        
        response = client.put(f"/balance/{balance_id}", json=update_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == "Updated Balance"
        assert data["balance_type"] == "COMPARISON"
        assert len(data["items"]) == 1
        assert data["total_amount"] == 150.00

    def test_archive_balance(self, client, test_data):
        """Test archiving a balance"""
        # Create balance first
        balance_data = {
            "name": "Balance to Archive",
            "items": []
        }
        
        create_response = client.post("/balance", json=balance_data)
        balance_id = create_response.json()["id"]
        
        # Archive the balance
        response = client.delete(f"/balance/{balance_id}")
        assert response.status_code == 200
        assert "archived successfully" in response.json()["message"]
        
        # Verify balance is archived
        response = client.get(f"/balance/{balance_id}")
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_compare_product_suppliers(self, client, test_data):
        """Test comparing suppliers for a specific product"""
        response = client.get(f"/balance/compare/{test_data['product1_id']}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["product_id"] == test_data["product1_id"]
        assert data["product_name"] == "Test Product 1"
        assert len(data["suppliers"]) == 2  # Two suppliers offer this product
        
        # Should be sorted by total cost (lowest first)
        suppliers = data["suppliers"]
        assert suppliers[0]["total_unit_cost"] <= suppliers[1]["total_unit_cost"]
        
        # Verify cost calculations
        # Supplier1: cost=80, shipping=10, total=90
        # Supplier2: cost=85, shipping=5, total=90
        # Or vice versa depending on which is cheaper
        for supplier in suppliers:
            expected_total = supplier["unit_cost"] + supplier["shipping_cost"]
            assert supplier["total_unit_cost"] == expected_total

    def test_compare_nonexistent_product(self, client):
        """Test comparing suppliers for non-existent product"""
        response = client.get("/balance/compare/99999")
        assert response.status_code == 404
        assert "Product not found" in response.json()["detail"]

    def test_quick_compare_products(self, client, test_data):
        """Test quick comparison of multiple products"""
        product_ids = [test_data["product1_id"], test_data["product2_id"]]
        
        response = client.post("/balance/quick-compare", json=product_ids)
        assert response.status_code == 200
        
        data = response.json()
        assert "comparisons" in data
        comparisons = data["comparisons"]
        assert len(comparisons) == 2
        
        # First product should have 2 suppliers
        product1_comparison = next(c for c in comparisons if c["product_id"] == test_data["product1_id"])
        assert product1_comparison["total_suppliers"] == 2
        assert product1_comparison["best_supplier"] is not None
        
        # Second product should have 1 supplier
        product2_comparison = next(c for c in comparisons if c["product_id"] == test_data["product2_id"])
        assert product2_comparison["total_suppliers"] == 1

    def test_balance_item_total_calculation(self, client, test_data):
        """Test that balance items calculate total cost correctly"""
        balance_data = {
            "name": "Cost Calculation Test",
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "supplier_id": test_data["supplier1_id"],
                    "quantity": 3,
                    "unit_price": 100.00,
                    "shipping_cost": 25.00
                }
            ]
        }
        
        response = client.post("/balance", json=balance_data)
        assert response.status_code == 200
        
        data = response.json()
        item = data["items"][0]
        
        # Total should be (unit_price + shipping_cost) * quantity
        # (100 + 25) * 3 = 375
        assert item["total_cost"] == 375.00
        assert data["total_amount"] == 375.00

    def test_search_balances(self, client, test_data):
        """Test searching balances by name/description"""
        # Create test balances
        balances_data = [
            {"name": "Solar Quote", "description": "Solar panel quotation"},
            {"name": "Wind Project", "description": "Wind turbine quotation"},
            {"name": "Battery Storage", "description": "Energy storage comparison"}
        ]
        
        for balance_data in balances_data:
            balance_data["items"] = []
            client.post("/balance", json=balance_data)
        
        # Search by name
        response = client.get("/balance?search=Solar")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Solar Quote"
        
        # Search by description
        response = client.get("/balance?search=quotation")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2  # Solar and Wind both have "quotation"

    def test_filter_by_balance_type(self, client, test_data):
        """Test filtering balances by type"""
        # Create balances with different types
        quotation_balance = {"name": "Quotation", "balance_type": "QUOTATION", "items": []}
        comparison_balance = {"name": "Comparison", "balance_type": "COMPARISON", "items": []}
        analysis_balance = {"name": "Analysis", "balance_type": "ANALYSIS", "items": []}
        
        client.post("/balance", json=quotation_balance)
        client.post("/balance", json=comparison_balance)
        client.post("/balance", json=analysis_balance)
        
        # Filter by QUOTATION
        response = client.get("/balance?balance_type=QUOTATION")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["balance_type"] == "QUOTATION"
        
        # Filter by COMPARISON
        response = client.get("/balance?balance_type=COMPARISON")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["balance_type"] == "COMPARISON"

    def test_shipping_cost_calculation_methods(self, client, test_data):
        """Test that different shipping methods are calculated correctly in comparisons"""
        response = client.get(f"/balance/compare/{test_data['product1_id']}")
        assert response.status_code == 200
        
        data = response.json()
        suppliers = data["suppliers"]
        
        # Find the DIRECT method supplier
        direct_supplier = next(s for s in suppliers if s["shipping_method"] == "DIRECT")
        assert direct_supplier["shipping_cost"] == 10.00  # shipping_cost_direct
        
        # Verify total calculation
        expected_total = direct_supplier["unit_cost"] + direct_supplier["shipping_cost"]
        assert direct_supplier["total_unit_cost"] == expected_total

if __name__ == "__main__":
    pytest.main([__file__, "-v"])