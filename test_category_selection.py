import os
from dotenv import load_dotenv
from quotation_processor import QuotationProcessor

# Load environment variables
load_dotenv()

def test_category_selection():
    """Test the improved category selection logic with problematic products."""
    
    # Initialize the processor
    anthropic_api_key = os.getenv('ANTHROPIC_API_KEY')
    if not anthropic_api_key:
        print("Error: ANTHROPIC_API_KEY not found in environment variables")
        return
    
    processor = QuotationProcessor(anthropic_api_key)
    
    # Test products that were incorrectly categorized as Category 3
    test_products = [
        {
            "name": "TRAMPA AMARILLA INDIVIDUAL CON PEGAMENTO HOT MELT Y CUADROS PARA ESTADISTICA 12.7 x 21.7 x .6mm",
            "description": "Yellow sticky trap for pest control"
        },
        {
            "name": "TRAMPA AZUL INDIVIDUAL CON PEGAMENTO HOT MELT Y CUADROS PARA ESTADISTICA 12.7 x 21.7 x .6mm",
            "description": "Blue sticky trap for pest control"
        },
        {
            "name": "BLANCO 25% SOMBRA CERRADO INVERNADERO HIDROPONICO ALIANZA ROLLO CERRADO CONTROL HUMEDAD 24 BLANCO 25% 620 100 720",
            "description": "25% white shade cloth for greenhouse"
        },
        {
            "name": "CINTA POLIPATCH 4\" 10M ALIANZA",
            "description": "4 inch repair tape 10 meters"
        },
        {
            "name": "CINTA POLIPATCH 4\" 30M ALIANZA",
            "description": "4 inch repair tape 30 meters"
        },
        {
            "name": "RAFIA NEGRA T1200 4.5KG ALIANZA",
            "description": "Black twine T1200 4.5kg"
        }
    ]
    
    print("Testing Category Selection Logic")
    print("=" * 60)
    
    for i, product in enumerate(test_products, 1):
        print(f"\n{i}. Product: {product['name']}")
        print(f"   Description: {product['description']}")
        
        # Test keyword-based category suggestion
        suggested_category = processor.get_suggested_category(product['name'], product['description'])
        print(f"   Keyword Analysis Suggests: Category {suggested_category}")
        
        # Expected categories based on our improvements
        expected_categories = {
            "TRAMPA": 4,  # Traps should go to Category 4 (pest control/protection)
            "SOMBRA": 1,  # Shade cloth should go to Category 1 (greenhouse)
            "CINTA": 14,  # Tape should go to Category 14 (hoses/pipes)
            "RAFIA": 1    # Twine should go to Category 1 (greenhouse)
        }
        
        # Determine expected category
        expected = None
        for keyword, cat_id in expected_categories.items():
            if keyword in product['name'].upper():
                expected = cat_id
                break
        
        if expected:
            print(f"   Expected Category: {expected}")
            if suggested_category == expected:
                print(f"   ✅ CORRECT: Keyword analysis matches expected category")
            else:
                print(f"   ❌ INCORRECT: Expected {expected}, got {suggested_category}")
        else:
            print(f"   ⚠️  No specific expectation defined")
        
        print("-" * 60)

if __name__ == "__main__":
    test_category_selection() 