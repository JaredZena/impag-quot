#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for USD currency support in quotation processing
"""

import json
import tempfile
import os
from utils.currency_utils import CurrencyUtils
from services.exchange_rate_service import exchange_rate_service

def test_currency_detection():
    """Test currency detection from text"""
    print("Testing currency detection...")
    
    # Test USD detection
    usd_texts = [
        "Product A: $25.50 USD",
        "Price: 15 dollars per unit",
        "Cost: US$ 100.00",
        "USD 50.00 per piece",
        "Precio: 25 dólares americanos"
    ]
    
    for text in usd_texts:
        detected = CurrencyUtils.detect_currency(text)
        print(f"Text: '{text}' -> Detected: {detected}")
        assert detected == "USD", f"Expected USD, got {detected}"
    
    # Test MXN detection
    mxn_texts = [
        "Producto A: $500 pesos",
        "Precio: 1000 pesos mexicanos",
        "Costo: $250 MXN",
        "Precio: 50 pesos"
    ]
    
    for text in mxn_texts:
        detected = CurrencyUtils.detect_currency(text)
        print(f"Text: '{text}' -> Detected: {detected}")
        assert detected == "MXN", f"Expected MXN, got {detected}"
    
    print("✅ Currency detection tests passed!")

def test_currency_parsing():
    """Test currency value parsing"""
    print("\nTesting currency value parsing...")
    
    # Test USD parsing
    usd_values = ["$25.50", "USD 100.00", "US$ 15.50", "25.50 dollars"]
    for value in usd_values:
        parsed = CurrencyUtils.parse_currency_value(value, "USD")
        print(f"Value: '{value}' -> Parsed: {parsed}")
        assert parsed is not None, f"Failed to parse {value}"
    
    # Test MXN parsing
    mxn_values = ["$500.00", "MXN 1000.00", "500 pesos"]
    for value in mxn_values:
        parsed = CurrencyUtils.parse_currency_value(value, "MXN")
        print(f"Value: '{value}' -> Parsed: {parsed}")
        assert parsed is not None, f"Failed to parse {value}"
    
    print("✅ Currency parsing tests passed!")

def test_currency_conversion():
    """Test currency conversion"""
    print("\nTesting currency conversion...")
    
    from decimal import Decimal
    
    # Test USD to MXN conversion
    usd_amount = Decimal("25.50")
    converted = CurrencyUtils.convert_currency(usd_amount, "USD", "MXN")
    print(f"USD {usd_amount} -> MXN {converted}")
    assert converted is not None, "Conversion failed"
    assert converted > usd_amount, "MXN should be higher than USD"
    
    # Test MXN to USD conversion
    mxn_amount = Decimal("500.00")
    converted = CurrencyUtils.convert_currency(mxn_amount, "MXN", "USD")
    print(f"MXN {mxn_amount} -> USD {converted}")
    assert converted is not None, "Conversion failed"
    assert converted < mxn_amount, "USD should be lower than MXN"
    
    print("✅ Currency conversion tests passed!")

def test_exchange_rate_service():
    """Test exchange rate service"""
    print("\nTesting exchange rate service...")
    
    # Test getting exchange rate
    rate = exchange_rate_service.get_exchange_rate("USD", "MXN")
    print(f"USD to MXN rate: {rate}")
    assert rate is not None, "Exchange rate should not be None"
    assert rate > 1, "USD to MXN rate should be greater than 1"
    
    # Test rate info
    rate_info = exchange_rate_service.get_rate_info("USD", "MXN")
    print(f"Rate info: {rate_info}")
    assert "rate" in rate_info
    assert "source" in rate_info
    assert "timestamp" in rate_info
    
    print("✅ Exchange rate service tests passed!")

def test_sample_usd_quotation():
    """Test processing a sample USD quotation"""
    print("\nTesting sample USD quotation processing...")
    
    # Create a sample USD quotation text
    sample_quotation = """
    QUOTATION - US SUPPLIER
    
    Product List:
    
    1. Solar Panel 100W - $45.00 USD per unit
    2. Inverter 1000W - $120.00 USD per unit  
    3. Battery 12V 100Ah - $85.00 USD per unit
    4. MC4 Connectors - $2.50 USD per pair
    
    All prices in US Dollars (USD)
    Shipping: Direct delivery
    """
    
    # Test currency detection
    detected_currency = CurrencyUtils.detect_currency(sample_quotation)
    print(f"Detected currency: {detected_currency}")
    assert detected_currency == "USD", f"Expected USD, got {detected_currency}"
    
    # Test parsing individual prices
    prices = ["$45.00", "$120.00", "$85.00", "$2.50"]
    for price in prices:
        parsed = CurrencyUtils.parse_currency_value(price, "USD")
        print(f"Price: {price} -> Parsed: {parsed}")
        assert parsed is not None, f"Failed to parse {price}"
    
    print("✅ Sample USD quotation tests passed!")

def main():
    """Run all tests"""
    print("🧪 Testing USD Currency Support")
    print("=" * 50)
    
    try:
        test_currency_detection()
        test_currency_parsing()
        test_currency_conversion()
        test_exchange_rate_service()
        test_sample_usd_quotation()
        
        print("\n🎉 All USD currency support tests passed!")
        print("\n📋 Summary:")
        print("✅ Currency detection working")
        print("✅ Currency parsing working")
        print("✅ Currency conversion working")
        print("✅ Exchange rate service working")
        print("✅ Sample USD quotation processing working")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        raise

if __name__ == "__main__":
    main()
