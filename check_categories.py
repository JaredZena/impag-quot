from models import SessionLocal, ProductCategory

def check_categories():
    session = SessionLocal()
    try:
        categories = session.query(ProductCategory).all()
        
        if not categories:
            print("No categories found in the database.")
            print("\nYou need to create at least one category before processing quotations.")
            print("Here's how to create a category using the API:")
            print("""
curl -X POST "http://localhost:8000/products/categories" \\
     -H "Content-Type: application/json" \\
     -d '{
           "name": "Your Category Name",
           "slug": "your-category-slug"
         }'
            """)
        else:
            print("Existing categories:")
            print("=" * 50)
            for category in categories:
                print(f"ID: {category.id}")
                print(f"Name: {category.name}")
                print(f"Slug: {category.slug}")
                print("-" * 30)
            
            print("\nWhen processing quotations, use one of these category IDs.")
            print("The default category_id is 3, but you should verify this exists.")
            
    finally:
        session.close()

if __name__ == "__main__":
    check_categories() 