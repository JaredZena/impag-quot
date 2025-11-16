#!/usr/bin/env python3
"""
Test script for stock management API endpoints
"""

import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"  # Adjust this to your API URL
API_KEY = None  # Add your API key if authentication is required

def test_get_stock():
    """Test getting products in stock"""
    print("🧪 Testing GET /products/stock")
    
    response = requests.get(f"{BASE_URL}/products/stock", params={
        "min_stock": 1,
        "limit": 10
    })
    
    if response.status_code == 200:
        data = response.json()
        if data.get("success"):
            products = data.get("data", {}).get("products", [])
            print(f"✅ Found {len(products)} products in stock")
            for product in products[:3]:  # Show first 3
                print(f"   📦 {product['name']}: {product['stock']} units @ ${product['price'] or 0}")
        else:
            print(f"❌ API returned error: {data.get('error')}")
    else:
        print(f"❌ HTTP {response.status_code}: {response.text}")
    print()

def test_stock_filters():
    """Test stock filtering on main products endpoint"""
    print("🧪 Testing stock filters on GET /products")
    
    filters = [
        ("in_stock", {"min_stock": 1}),
        ("out_of_stock", {"max_stock": 0}),
        ("low_stock", {"max_stock": 9})
    ]
    
    for filter_name, params in filters:
        response = requests.get(f"{BASE_URL}/products", params=params)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                products = data.get("data", [])
                print(f"✅ {filter_name}: Found {len(products)} products")
            else:
                print(f"❌ {filter_name}: API error - {data.get('error')}")
        else:
            print(f"❌ {filter_name}: HTTP {response.status_code}")
    print()

def test_update_stock():
    """Test updating stock for a product"""
    print("🧪 Testing PATCH /products/{id}/stock")
    
    # First, get a product to update
    response = requests.get(f"{BASE_URL}/products", params={"limit": 1})
    
    if response.status_code != 200:
        print("❌ Could not fetch products for testing")
        return
    
    data = response.json()
    if not data.get("success") or not data.get("data"):
        print("❌ No products found for testing")
        return
    
    product = data["data"][0]
    product_id = product["id"]
    original_stock = product["stock"]
    
    print(f"📦 Testing with product: {product['name']} (ID: {product_id})")
    print(f"   Original stock: {original_stock}")
    
    # Update stock
    new_stock = original_stock + 10
    update_response = requests.patch(
        f"{BASE_URL}/products/{product_id}/stock",
        params={"stock": new_stock, "price": 25.99}
    )
    
    if update_response.status_code == 200:
        update_data = update_response.json()
        if update_data.get("success"):
            print(f"✅ Stock updated successfully")
            print(f"   New stock: {update_data['data']['stock']}")
            print(f"   New price: ${update_data['data']['price']}")
        else:
            print(f"❌ Update failed: {update_data.get('error')}")
    else:
        print(f"❌ HTTP {update_response.status_code}: {update_response.text}")
    print()

def test_bulk_update():
    """Test bulk stock update"""
    print("🧪 Testing POST /products/stock/bulk-update")
    
    # Get a few products to update
    response = requests.get(f"{BASE_URL}/products", params={"limit": 2})
    
    if response.status_code != 200:
        print("❌ Could not fetch products for testing")
        return
    
    data = response.json()
    if not data.get("success") or len(data.get("data", [])) < 2:
        print("❌ Need at least 2 products for bulk update test")
        return
    
    products = data["data"][:2]
    
    # Prepare bulk update
    updates = []
    for product in products:
        updates.append({
            "product_id": product["id"],
            "stock": product["stock"] + 5,
            "price": 15.99
        })
    
    bulk_data = {"updates": updates}
    
    bulk_response = requests.post(
        f"{BASE_URL}/products/stock/bulk-update",
        json=bulk_data,
        headers={"Content-Type": "application/json"}
    )
    
    if bulk_response.status_code == 200:
        bulk_result = bulk_response.json()
        if bulk_result.get("success"):
            updated_count = bulk_result["data"]["updated_count"]
            print(f"✅ Bulk update successful: {updated_count} products updated")
        else:
            print(f"❌ Bulk update failed: {bulk_result.get('error')}")
    else:
        print(f"❌ HTTP {bulk_response.status_code}: {bulk_response.text}")
    print()

def main():
    print("🚀 Testing Stock Management API")
    print("=" * 50)
    
    try:
        # Test basic connectivity
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ API is accessible")
        else:
            print(f"⚠️  API responded with status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to API. Make sure the server is running.")
        return
    except requests.exceptions.Timeout:
        print("❌ API request timed out")
        return
    
    print()
    
    # Run tests
    test_get_stock()
    test_stock_filters()
    test_update_stock()
    test_bulk_update()
    
    print("🏁 Testing completed!")

if __name__ == "__main__":
    main()

