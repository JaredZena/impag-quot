#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Match Suppliers to Products Script

This script:
1. Reads products_needing_suppliers.csv
2. Extracts supplier names from product names (in parentheses)
3. Uses Claude AI to match extracted names to actual suppliers in database
4. Creates supplier products for successful matches
5. Exports results to CSV for review

Usage:
    python migrations/match_suppliers_to_products.py [--dry-run] [--create-supplier-products]
"""

import os
import sys
import csv
import re
import json
from typing import List, Dict, Optional, Tuple
from decimal import Decimal
from dotenv import load_dotenv
import anthropic

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables FIRST (before importing models that might need DB connection)
load_dotenv()

# Now import models (this might try to connect to DB)
try:
    from models import Product, Supplier, SupplierProduct, get_db, SessionLocal
    from sqlalchemy.orm import Session
except Exception as e:
    print(f"❌ Error importing models: {e}", flush=True)
    raise

def get_database_url():
    """Get database URL from environment."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ Error: DATABASE_URL environment variable not set")
        sys.exit(1)
    return database_url

def extract_supplier_from_name(product_name: str) -> Optional[str]:
    """
    Extract supplier name from product name.
    Looks for text in parentheses at the end of the name.
    
    Examples:
    - "PRODUCT NAME (POPUSA)" -> "POPUSA"
    - "Product (LUPIN "LA CHOLE")" -> "LUPIN "LA CHOLE""
    - "Product (Doña Paula)" -> "Doña Paula"
    """
    # Pattern: text in parentheses, possibly at the end
    # Handle nested quotes: (LUPIN "LA CHOLE")
    pattern = r'\(([^)]+)\)\s*$'
    match = re.search(pattern, product_name)
    if match:
        supplier = match.group(1).strip()
        # Remove quotes if present
        supplier = supplier.strip('"').strip("'")
        return supplier
    return None

def get_all_suppliers(session: Session) -> List[Dict]:
    """Get all active suppliers from database."""
    suppliers = session.query(Supplier).filter(
        Supplier.archived_at.is_(None)
    ).all()
    
    return [
        {
            "id": s.id,
            "name": s.name,
            "common_name": s.common_name,
            "legal_name": s.legal_name,
            "rfc": s.rfc,
            "display_name": s.common_name or s.name or s.legal_name or ""
        }
        for s in suppliers
    ]

def match_supplier_with_ai(
    extracted_supplier: str,
    suppliers: List[Dict],
    client: anthropic.Anthropic
) -> Optional[Dict]:
    """
    Use Claude AI to match extracted supplier name to actual supplier.
    
    Returns:
        Supplier dict if match found, None otherwise
    """
    # Build supplier list for AI
    suppliers_text = "\n".join([
        f"- ID {s['id']}: {s['name']} (common: {s['common_name'] or 'N/A'}, legal: {s['legal_name'] or 'N/A'})"
        for s in suppliers
    ])
    
    prompt = f"""You are helping match supplier names extracted from product names to actual suppliers in a database.

EXTRACTED SUPPLIER NAME FROM PRODUCT: "{extracted_supplier}"

AVAILABLE SUPPLIERS IN DATABASE:
{suppliers_text}

TASK:
1. Find the best matching supplier from the list above
2. Consider:
   - Exact matches
   - Partial matches (e.g., "POPUSA" might match "Popusa" or "POPUSA S.A.")
   - Common name variations
   - Abbreviations (e.g., "LAMSA" might match "Lamsa" or "LAMSA Industries")
   - Nicknames or trading names
   - Case-insensitive matching

3. If you find a match, return ONLY the supplier ID number (just the number, nothing else)
4. If NO match is found, return "NO_MATCH"

EXAMPLES:
- "POPUSA" might match supplier ID 5 if that supplier's name contains "POPUSA"
- "LUPIN LA CHOLE" might match supplier ID 12 if that supplier's name or common_name contains "LUPIN" or "CHOLE"
- "Doña Paula" might match supplier ID 8 if that supplier's name contains "Paula" or "Dona Paula"

Return ONLY the supplier ID number if match found, or "NO_MATCH" if no match.
Do not include any explanation or additional text."""

    try:
        # Make API call with timeout handling
        # Note: Anthropic client doesn't support timeout parameter directly
        # We'll rely on the HTTP client's default timeout
        import threading
        import queue
        
        result_queue = queue.Queue()
        error_queue = queue.Queue()
        
        def make_api_call():
            try:
                response = client.messages.create(
                    model="claude-3-5-haiku-20241022",  # Using Haiku for speed and cost efficiency
                    max_tokens=5000,
                    temperature=0,
                    messages=[{"role": "user", "content": prompt}]
                )
                result_queue.put(response)
            except Exception as e:
                error_queue.put(e)
        
        # Start API call in a thread
        api_thread = threading.Thread(target=make_api_call, daemon=True)
        api_thread.start()
        
        # Show progress dots while waiting (check every 1 second for faster feedback)
        import time
        elapsed = 0
        timeout = 15.0  # Reduced timeout since Haiku is faster
        check_interval = 1.0  # Check every second
        
        while api_thread.is_alive() and elapsed < timeout:
            time.sleep(check_interval)
            elapsed += check_interval
            if elapsed < timeout:
                print(".", end="", flush=True)
        
        # Final join to ensure thread completes
        api_thread.join(timeout=max(0, timeout - elapsed))
        
        if api_thread.is_alive():
            print(" (timeout)", flush=True)
            return None  # Timeout
        
        # Check for errors
        if not error_queue.empty():
            error = error_queue.get()
            raise error
        
        # Get response
        if result_queue.empty():
            return None
        
        response = result_queue.get()
        
        result = response.content[0].text.strip()
        
        # Remove any markdown formatting
        result = result.replace("```", "").strip()
        
        if result == "NO_MATCH" or not result.isdigit():
            return None
        
        supplier_id = int(result)
        # Find supplier by ID
        matched_supplier = next((s for s in suppliers if s['id'] == supplier_id), None)
        return matched_supplier
        
    except TimeoutError as e:
        print(f"   ⚠️  AI matching timed out: {e}", flush=True)
        return None
    except Exception as e:
        print(f"   ⚠️  AI matching error: {e}", flush=True)
        return None

def create_supplier_product(
    session: Session,
    product_id: int,
    supplier_id: int,
    product_price: Optional[float] = None
) -> Optional[SupplierProduct]:
    """
    Create a supplier product relationship.
    
    Returns:
        Created SupplierProduct or None if already exists
    """
    # Check if supplier product already exists
    existing = session.query(SupplierProduct).filter(
        SupplierProduct.product_id == product_id,
        SupplierProduct.supplier_id == supplier_id,
        SupplierProduct.archived_at.is_(None)
    ).first()
    
    if existing:
        return None  # Already exists
    
    # Create new supplier product
    supplier_product = SupplierProduct(
        supplier_id=supplier_id,
        product_id=product_id,
        cost=Decimal(str(product_price)) if product_price else None,
        currency='MXN',  # Default to MXN
        stock=0,  # Stock will be migrated separately
        is_active=True
    )
    
    session.add(supplier_product)
    session.flush()
    return supplier_product

def process_products(
    csv_path: str,
    dry_run: bool = True,
    create_products: bool = False
) -> Dict:
    """
    Process products and match suppliers.
    
    Args:
        csv_path: Path to products_needing_suppliers.csv
        dry_run: If True, don't create supplier products, just match
        create_products: If True, create supplier products for matches
    
    Returns:
        Dictionary with results
    """
    # Initialize Claude client
    claude_api_key = os.getenv("CLAUDE_API_KEY")
    if not claude_api_key:
        print("❌ Error: CLAUDE_API_KEY environment variable not set")
        sys.exit(1)
    
    client = anthropic.Anthropic(api_key=claude_api_key)
    
    # Get database session
    session = SessionLocal()
    
    try:
        # Get all suppliers
        print("📋 Loading suppliers from database...", flush=True)
        suppliers = get_all_suppliers(session)
        print(f"   ✅ Found {len(suppliers)} active suppliers", flush=True)
        
        if len(suppliers) == 0:
            print("   ⚠️  WARNING: No suppliers found in database!", flush=True)
            return {"error": "No suppliers in database"}
        
        # Show sample suppliers
        print(f"\n   Sample suppliers:", flush=True)
        for s in suppliers[:5]:
            print(f"      - ID {s['id']}: {s['name']}", flush=True)
        if len(suppliers) > 5:
            print(f"      ... and {len(suppliers) - 5} more", flush=True)
        
        # Read CSV
        print(f"\n📖 Reading products from {csv_path}...", flush=True)
        if not os.path.exists(csv_path):
            print(f"   ❌ Error: File not found: {csv_path}", flush=True)
            return {"error": f"CSV file not found: {csv_path}"}
        
        products = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                products.append(row)
        
        print(f"   ✅ Found {len(products)} products needing suppliers", flush=True)
        
        if len(products) == 0:
            print("   ⚠️  WARNING: No products to process!", flush=True)
            return {"error": "No products in CSV"}
        
        # Process each product
        results = {
            "total": len(products),
            "extracted_suppliers": 0,
            "matched": 0,
            "no_match": 0,
            "no_supplier_in_name": 0,
            "created": 0,
            "already_exists": 0,
            "matches": [],
            "no_matches": []
        }
        
        print("\n🔄 Processing products...", flush=True)
        print("=" * 80, flush=True)
        
        for i, product in enumerate(products, 1):
            try:
                product_id = int(product['product_id'])
                product_name = product['product_name']
                stock = int(product['stock']) if product['stock'] else 0
                price = float(product['price']) if product['price'] and product['price'].strip() else None
                
                print(f"\n[{i}/{len(products)}] Product ID {product_id}: {product_name[:60]}...", flush=True)
                print(f"   Stock: {stock}, Price: ${price or 'N/A'}", flush=True)
                
                # Extract supplier from name
                extracted_supplier = extract_supplier_from_name(product_name)
                
                if not extracted_supplier:
                    print(f"   ⚠️  No supplier found in product name", flush=True)
                    results["no_supplier_in_name"] += 1
                    results["no_matches"].append({
                        "product_id": product_id,
                        "product_name": product_name,
                        "stock": stock,
                        "reason": "No supplier in product name"
                    })
                    continue
                
                results["extracted_suppliers"] += 1
                print(f"   📝 Extracted supplier: '{extracted_supplier}'", flush=True)
                
                # Match with AI
                try:
                    print(f"   🤖 Matching with AI...", end="", flush=True)
                    matched_supplier = match_supplier_with_ai(extracted_supplier, suppliers, client)
                    print("", flush=True)  # New line after AI call completes
                except Exception as e:
                    print(f"", flush=True)  # New line
                    print(f"   ⚠️  Error during AI matching: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    matched_supplier = None
                
                if not matched_supplier:
                    print(f"   ❌ No match found", flush=True)
                    results["no_match"] += 1
                    results["no_matches"].append({
                        "product_id": product_id,
                        "product_name": product_name,
                        "extracted_supplier": extracted_supplier,
                        "stock": stock,
                        "reason": "No matching supplier found"
                    })
                    continue
                
                print(f"   ✅ Matched to: {matched_supplier['name']} (ID: {matched_supplier['id']})", flush=True)
                results["matched"] += 1
                
                match_info = {
                    "product_id": product_id,
                    "product_name": product_name,
                    "extracted_supplier": extracted_supplier,
                    "matched_supplier_id": matched_supplier['id'],
                    "matched_supplier_name": matched_supplier['name'],
                    "stock": stock,
                    "price": price
                }
                
                # Create supplier product if requested
                if create_products and not dry_run:
                    print(f"   🔨 Creating supplier product...", flush=True)
                    supplier_product = create_supplier_product(
                        session,
                        product_id,
                        matched_supplier['id'],
                        price
                    )
                    
                    if supplier_product:
                        print(f"   ✅ Created supplier product ID: {supplier_product.id}", flush=True)
                        results["created"] += 1
                        match_info["supplier_product_id"] = supplier_product.id
                        match_info["status"] = "CREATED"
                    else:
                        print(f"   ⚠️  Supplier product already exists", flush=True)
                        results["already_exists"] += 1
                        match_info["status"] = "ALREADY_EXISTS"
                else:
                    match_info["status"] = "MATCHED (dry run)"
                    print(f"   💡 Would create supplier product (dry run)", flush=True)
                
                results["matches"].append(match_info)
                
            except Exception as e:
                # Catch any unexpected errors during product processing
                print(f"\n   ❌ Unexpected error processing product {i}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                print(f"   ⚠️  Continuing to next product...", flush=True)
                continue
        
        # Commit if creating products
        if create_products and not dry_run:
            session.commit()
            print("\n✅ Changes committed to database", flush=True)
        else:
            session.rollback()
            print("\n⚠️  DRY RUN - No changes committed", flush=True)
        
        return results
        
    except Exception as e:
        session.rollback()
        import traceback
        print(f"\n❌ Error: {e}", flush=True)
        print(f"Traceback:", flush=True)
        traceback.print_exc()
        raise
    finally:
        session.close()

def export_results(results: Dict, output_path: str):
    """Export results to CSV files."""
    # Export matches
    matches_path = output_path.replace('.csv', '_matches.csv')
    with open(matches_path, 'w', newline='', encoding='utf-8') as f:
        if results["matches"]:
            writer = csv.DictWriter(f, fieldnames=results["matches"][0].keys())
            writer.writeheader()
            writer.writerows(results["matches"])
    
    # Export no matches
    no_matches_path = output_path.replace('.csv', '_no_matches.csv')
    with open(no_matches_path, 'w', newline='', encoding='utf-8') as f:
        if results["no_matches"]:
            writer = csv.DictWriter(f, fieldnames=results["no_matches"][0].keys())
            writer.writeheader()
            writer.writerows(results["no_matches"])
    
    print(f"\n📄 Results exported:")
    print(f"   Matches: {matches_path}")
    print(f"   No matches: {no_matches_path}")

def main():
    """Main function."""
    print("🚀 Script starting...", flush=True)
    import argparse
    
    print("📦 Parsing arguments...", flush=True)
    parser = argparse.ArgumentParser(description='Match suppliers to products using AI')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry run mode - match but do not create supplier products'
    )
    parser.add_argument(
        '--create-supplier-products',
        action='store_true',
        help='Create supplier products for matched suppliers (requires --no-dry-run)'
    )
    parser.add_argument(
        '--csv-path',
        default='migrations/products_needing_suppliers.csv',
        help='Path to products CSV file'
    )
    
    args = parser.parse_args()
    print(f"✅ Arguments parsed. Dry run: {args.dry_run}, Create products: {args.create_supplier_products}", flush=True)
    
    # Validate arguments
    if args.create_supplier_products and args.dry_run:
        print("❌ Error: Cannot use --create-supplier-products with --dry-run", flush=True)
        print("   Use --create-supplier-products without --dry-run to create products", flush=True)
        sys.exit(1)
    
    print("📂 Resolving CSV path...", flush=True)
    csv_path = os.path.join(
        os.path.dirname(__file__),
        '..',
        args.csv_path
    )
    csv_path = os.path.abspath(csv_path)
    
    print(f"   CSV path: {csv_path}", flush=True)
    if not os.path.exists(csv_path):
        print(f"❌ Error: CSV file not found: {csv_path}", flush=True)
        sys.exit(1)
    
    print("✅ CSV file exists", flush=True)
    print("=" * 80, flush=True)
    print("🔍 SUPPLIER MATCHING SCRIPT", flush=True)
    print("=" * 80, flush=True)
    print(f"CSV File: {csv_path}", flush=True)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE' if args.create_supplier_products else 'MATCH ONLY'}", flush=True)
    print("=" * 80, flush=True)
    
    if args.create_supplier_products:
        response = input("\n⚠️  This will CREATE supplier products in the database. Continue? (y/N): ")
        if response.lower() != 'y':
            print("❌ Cancelled.")
            sys.exit(0)
    
    # Process products
    results = process_products(
        csv_path,
        dry_run=args.dry_run,
        create_products=args.create_supplier_products
    )
    
    # Print summary
    print("\n" + "=" * 80, flush=True)
    print("📊 SUMMARY", flush=True)
    print("=" * 80, flush=True)
    print(f"Total products processed: {results.get('total', 0)}", flush=True)
    print(f"Products with supplier in name: {results.get('extracted_suppliers', 0)}", flush=True)
    print(f"Products without supplier in name: {results.get('no_supplier_in_name', 0)}", flush=True)
    print(f"", flush=True)
    print(f"✅ Matched: {results.get('matched', 0)}", flush=True)
    print(f"❌ No match found: {results.get('no_match', 0)}", flush=True)
    print(f"", flush=True)
    if args.create_supplier_products:
        print(f"🔨 Supplier products created: {results.get('created', 0)}", flush=True)
        print(f"⚠️  Already existed: {results.get('already_exists', 0)}", flush=True)
    
    # Export results
    if 'matches' in results and 'no_matches' in results:
        output_dir = os.path.dirname(csv_path)
        export_results(results, os.path.join(output_dir, 'supplier_matching_results.csv'))
        print(f"\n📄 Results exported to {output_dir}", flush=True)
    
    print("\n✅ Processing complete!", flush=True)

if __name__ == "__main__":
    main()

