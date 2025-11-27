import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import SupplierProduct, Supplier, get_db
from config import database_url, openai_api_key
import openai
from pgvector.sqlalchemy import Vector

# Initialize OpenAI client
client = openai.OpenAI(api_key=openai_api_key)

def get_db_connection():
    # Parse the database URL to get the endpoint ID
    from urllib.parse import urlparse, parse_qs, urlencode
    
    parsed_url = urlparse(database_url)
    endpoint_id = parsed_url.hostname.split('.')[0]
    
    query_params = parse_qs(parsed_url.query)
    query_params['options'] = [f'endpoint={endpoint_id}']
    new_query = urlencode(query_params, doseq=True)
    
    modified_url = parsed_url._replace(query=new_query).geturl()
    if not modified_url.startswith('postgresql+psycopg2://'):
        modified_url = modified_url.replace('postgresql://', 'postgresql+psycopg2://')
    
    engine = create_engine(modified_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def run_migration():
    """Run the SQL migration to add vector columns"""
    print("Running migration...")
    db = get_db_connection()
    try:
        with open('migrations/add_embeddings_vector.sql', 'r') as f:
            sql = f.read()
            # Split by semicolon to run statements individually
            statements = sql.split(';')
            for stmt in statements:
                if stmt.strip():
                    print(f"Executing: {stmt.strip()[:50]}...")
                    db.execute(text(stmt))
            db.commit()
            print("✅ Migration completed successfully")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.rollback()
    finally:
        db.close()

def generate_embedding(text):
    """Generate embedding using OpenAI"""
    try:
        response = client.embeddings.create(
            input=text,
            model="text-embedding-ada-002"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None

def process_supplier_products():
    """Generate embeddings for supplier products that don't have them"""
    db = get_db_connection()
    try:
        # Get products without embeddings or not marked as embedded
        products = db.query(SupplierProduct).filter(
            (SupplierProduct.embedding == None) | (SupplierProduct.embedded == False)
        ).all()
        
        print(f"Found {len(products)} products to process")
        
        count = 0
        for sp in products:
            # Construct rich text representation for embedding
            # Include name, description, specs, category, supplier name
            specs_str = ""
            if sp.specifications:
                specs_str = ", ".join([f"{k}: {v}" for k, v in sp.specifications.items()])
            
            supplier_name = sp.supplier.name if sp.supplier else "Unknown Supplier"
            
            # Text to embed
            text_to_embed = f"Product: {sp.name or sp.product.name}. "
            text_to_embed += f"Description: {sp.description or sp.product.description or ''}. "
            text_to_embed += f"Specs: {specs_str}. "
            text_to_embed += f"Supplier: {supplier_name}. "
            text_to_embed += f"Category: {sp.product.category.name if sp.product and sp.product.category else ''}."
            
            # Clean up text
            text_to_embed = " ".join(text_to_embed.split())
            
            print(f"Processing ({count+1}/{len(products)}): {sp.name or sp.product.name}")
            
            embedding = generate_embedding(text_to_embed)
            
            if embedding:
                sp.embedding = embedding
                sp.embedded = True
                count += 1
                
                # Commit every 10 items to save progress
                if count % 10 == 0:
                    db.commit()
                    print(f"Saved {count} embeddings...")
                    time.sleep(0.5) # Rate limiting
            
        db.commit()
        print(f"✅ Completed! Processed {count} products.")
        
    except Exception as e:
        print(f"❌ Error processing products: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    # 1. Run migration first
    run_migration()
    
    # 2. Process products
    process_supplier_products()
