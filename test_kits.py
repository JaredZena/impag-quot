import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, get_db, Kit, KitItem, Product, ProductCategory, ProductUnit
from main import app
import json

# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_kits.db"
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
    """Create test data for kits testing"""
    db = TestingSessionLocal()
    
    try:
        # Create test category
        category = ProductCategory(name="Test Category", slug="test-category")
        db.add(category)
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
        db.commit()
        
        return {
            "category_id": category.id,
            "product1_id": product1.id,
            "product2_id": product2.id
        }
        
    finally:
        db.close()

class TestKitsAPI:
    
    def test_get_empty_kits_list(self, client):
        """Test getting empty kits list"""
        response = client.get("/kits")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_kit_success(self, client, test_data):
        """Test creating a kit successfully"""
        kit_data = {
            "name": "Solar Panel Kit",
            "description": "Complete solar panel installation kit",
            "sku": "SOLAR-KIT-001",
            "price": 500.00,
            "margin": 0.25,
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "quantity": 2,
                    "unit_price": 100.00,
                    "notes": "Main component"
                },
                {
                    "product_id": test_data["product2_id"],
                    "quantity": 1,
                    "unit_price": 200.00,
                    "notes": "Secondary component"
                }
            ]
        }
        
        response = client.post("/kits", json=kit_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == kit_data["name"]
        assert data["sku"] == kit_data["sku"]
        assert data["price"] == kit_data["price"]
        assert data["margin"] == kit_data["margin"]
        assert len(data["items"]) == 2
        assert data["calculated_cost"] == 400.00  # (100*2) + (200*1)
        assert data["is_active"] is True

    def test_create_kit_duplicate_sku(self, client, test_data):
        """Test creating kit with duplicate SKU fails"""
        kit_data = {
            "name": "First Kit",
            "sku": "DUPLICATE-SKU",
            "items": []
        }
        
        # Create first kit
        response = client.post("/kits", json=kit_data)
        assert response.status_code == 200
        
        # Try to create second kit with same SKU
        kit_data["name"] = "Second Kit"
        response = client.post("/kits", json=kit_data)
        assert response.status_code == 400
        assert "SKU already exists" in response.json()["detail"]

    def test_create_kit_invalid_product(self, client):
        """Test creating kit with invalid product fails"""
        kit_data = {
            "name": "Invalid Kit",
            "sku": "INVALID-KIT",
            "items": [
                {
                    "product_id": 99999,  # Non-existent product
                    "quantity": 1
                }
            ]
        }
        
        response = client.post("/kits", json=kit_data)
        assert response.status_code == 400
        assert "Product 99999 not found" in response.json()["detail"]

    def test_get_kit_by_id(self, client, test_data):
        """Test getting specific kit by ID"""
        # First create a kit
        kit_data = {
            "name": "Test Kit",
            "sku": "TEST-KIT-001",
            "price": 300.00,
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "quantity": 1
                }
            ]
        }
        
        create_response = client.post("/kits", json=kit_data)
        kit_id = create_response.json()["id"]
        
        # Get the kit
        response = client.get(f"/kits/{kit_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["id"] == kit_id
        assert data["name"] == kit_data["name"]
        assert data["sku"] == kit_data["sku"]
        assert len(data["items"]) == 1

    def test_get_nonexistent_kit(self, client):
        """Test getting non-existent kit returns 404"""
        response = client.get("/kits/99999")
        assert response.status_code == 404
        assert "Kit not found" in response.json()["detail"]

    def test_update_kit(self, client, test_data):
        """Test updating a kit"""
        # Create kit first
        kit_data = {
            "name": "Original Kit",
            "sku": "ORIGINAL-KIT",
            "price": 100.00,
            "items": []
        }
        
        create_response = client.post("/kits", json=kit_data)
        kit_id = create_response.json()["id"]
        
        # Update the kit
        update_data = {
            "name": "Updated Kit",
            "price": 150.00,
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "quantity": 1
                }
            ]
        }
        
        response = client.put(f"/kits/{kit_id}", json=update_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == "Updated Kit"
        assert data["price"] == 150.00
        assert len(data["items"]) == 1

    def test_update_nonexistent_kit(self, client):
        """Test updating non-existent kit returns 404"""
        update_data = {"name": "Updated Name"}
        response = client.put("/kits/99999", json=update_data)
        assert response.status_code == 404
        assert "Kit not found" in response.json()["detail"]

    def test_archive_kit(self, client, test_data):
        """Test archiving a kit"""
        # Create kit first
        kit_data = {
            "name": "Kit to Archive",
            "sku": "ARCHIVE-KIT",
            "items": []
        }
        
        create_response = client.post("/kits", json=kit_data)
        kit_id = create_response.json()["id"]
        
        # Archive the kit
        response = client.delete(f"/kits/{kit_id}")
        assert response.status_code == 200
        assert "archived successfully" in response.json()["message"]
        
        # Verify kit is archived (should still exist but not active)
        response = client.get(f"/kits/{kit_id}")
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_restore_kit(self, client, test_data):
        """Test restoring an archived kit"""
        # Create and archive kit
        kit_data = {
            "name": "Kit to Restore",
            "sku": "RESTORE-KIT", 
            "items": []
        }
        
        create_response = client.post("/kits", json=kit_data)
        kit_id = create_response.json()["id"]
        
        # Archive it
        client.delete(f"/kits/{kit_id}")
        
        # Restore it
        response = client.post(f"/kits/{kit_id}/restore")
        assert response.status_code == 200
        assert "restored successfully" in response.json()["message"]
        
        # Verify kit is restored
        response = client.get(f"/kits/{kit_id}")
        assert response.status_code == 200
        assert response.json()["is_active"] is True

    def test_search_kits(self, client, test_data):
        """Test searching kits by name/description/SKU"""
        # Create test kits
        kits_data = [
            {"name": "Solar Panel Kit", "sku": "SOLAR-001", "description": "Solar power kit"},
            {"name": "Wind Power Kit", "sku": "WIND-001", "description": "Wind energy kit"},
            {"name": "Battery Pack", "sku": "BATTERY-001", "description": "Energy storage"}
        ]
        
        for kit_data in kits_data:
            kit_data["items"] = []
            client.post("/kits", json=kit_data)
        
        # Search by name
        response = client.get("/kits?search=Solar")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Solar Panel Kit"
        
        # Search by SKU
        response = client.get("/kits?search=WIND")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["sku"] == "WIND-001"
        
        # Search by description
        response = client.get("/kits?search=energy")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2  # Wind and Battery both contain "energy"

    def test_filter_active_kits(self, client, test_data):
        """Test filtering kits by active status"""
        # Create active and inactive kits
        active_kit = {"name": "Active Kit", "sku": "ACTIVE-001", "items": []}
        inactive_kit = {"name": "Inactive Kit", "sku": "INACTIVE-001", "items": []}
        
        # Create kits
        active_response = client.post("/kits", json=active_kit)
        inactive_response = client.post("/kits", json=inactive_kit)
        
        # Archive one kit
        inactive_id = inactive_response.json()["id"]
        client.delete(f"/kits/{inactive_id}")
        
        # Filter for active only
        response = client.get("/kits?is_active=true")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Active Kit"
        
        # Filter for inactive only
        response = client.get("/kits?is_active=false")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Inactive Kit"

    def test_kit_cost_calculation(self, client, test_data):
        """Test automatic cost calculation for kits"""
        kit_data = {
            "name": "Cost Test Kit",
            "sku": "COST-TEST-001",
            "items": [
                {
                    "product_id": test_data["product1_id"],
                    "quantity": 3,
                    "unit_price": 50.00  # Override product price
                },
                {
                    "product_id": test_data["product2_id"],
                    "quantity": 2
                    # No unit_price override, should use product price (200.00)
                }
            ]
        }
        
        response = client.post("/kits", json=kit_data)
        assert response.status_code == 200
        
        data = response.json()
        # Should calculate: (50 * 3) + (200 * 2) = 150 + 400 = 550
        assert data["calculated_cost"] == 550.00

if __name__ == "__main__":
    pytest.main([__file__, "-v"])