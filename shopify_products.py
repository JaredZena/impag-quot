import requests
from rapidfuzz import process
import re

# List of common stop words to exclude
STOP_WORDS = {"y", "de", "para", "por", "con", "en", "a", "por", "el", "la", "los", "las", "un", "una", "unos", "unas", "del", "rollo", "cultivo", "m", "hectarea", "hectareas", "chile", "tomate", "maiz", "frijol", "manzana", "profundidad", "ancho", "largo", "superficie", "marca", "cal", "calibre", "pulgadas", "cm", "mm", "x", "grado", "agricola","agrícola","uso", "agua", "capacidades", "capacidad", "pieza", "piezas", "hasta", "m²"}

def clean_text(text):
    """Cleans the text by removing stop words and numbers."""
    text = text.lower().strip()
    words = text.split()
    filtered_words = [word for word in words if word not in STOP_WORDS and not word.isdigit()]
    return ' '.join(filtered_words)

def fetch_shopify_prices():
    base_url = "https://todoparaelcampo.com.mx/products.json?limit=200"
    response = requests.get(base_url)

    if response.status_code != 200:
        print(f"Error fetching Shopify API: {response.status_code}")
        return {}

    try:
        product_data = response.json()
        products = {}

        for product in product_data.get("products", []):
            title = product["title"].strip().lower()

            for variant in product.get("variants", []):
                variant_name = variant["title"].strip().lower()
                price = variant["price"]

                # Create a unique product name for each variant
                full_product_name = f"{title} {variant_name}" if variant_name != "default title" else title
                products[full_product_name] = {
                    "price": f"${price} MXN"
                }

        print(f"Fetched {len(products)} product variants from Shopify")
        return products

    except Exception as e:
        print(f"Error parsing Shopify JSON: {e}")
        return {}

def clean_title(title):
    """Returns the part of the title before the first hyphen ('-') and removes stop words."""
    cleaned_title = title.split('–')[0].split('-')[0].strip()
    # Remove stop words from the cleaned title
    return clean_text(cleaned_title)

def find_best_matching_products(query, shopify_prices, threshold=53):
    matched_products = {}

    # Clean the query by removing stop words and numbers
    cleaned_query = clean_text(query)
    
    for product in shopify_prices.keys():
        # Use the cleaned title for calculating similarity score
        clean_product = clean_title(product)
        similarity_score = process.extractOne(cleaned_query, [clean_product])[1]
        print(f"Similarity between '{cleaned_query}' and '{clean_product}': {similarity_score}")
        if similarity_score >= threshold:
            matched_products[product] = shopify_prices[product]

    return matched_products