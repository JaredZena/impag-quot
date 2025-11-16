import requests

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