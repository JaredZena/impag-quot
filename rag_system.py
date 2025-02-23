from embeddings import generate_embeddings
from pinecone_setup import index
from shopify_products import fetch_shopify_prices, find_best_matching_products
from claude_llm_setup import llm

def query_rag_system(query):
    print(f'🔹 Query Received: {query}')
    query_embedding = generate_embeddings([query])[0]
    results = index.query(vector=query_embedding, top_k=10, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    print(f'Context: {context}')

    shopify_prices = fetch_shopify_prices()
    matched_products = find_best_matching_products(query.lower(), shopify_prices)

    if matched_products:
        price_context = "\n".join([f"{product}: {price}" for product, price in matched_products.items()])
    else:
        price_context = "No se encontraron precios actualizados para los productos solicitados."

    print(f'🔹 Matched Products: {matched_products}')

    prompt = (f"Genera una cotización de Impag basada en el catálogo de productos y cotizaciones previas, "
              f"asegurate de incluir especificaciones completas de los productos, en la descripcion. "
              f"Si el usuario proporciona un término general (ej. geomembranas, sistemas de riego, drones agrícolas), "
              f"genera múltiples opciones con diferentes tipos, especificaciones y precios cuando estén disponibles. "
              f"Si el usuario proporciona datos tecnicos para calcular los productos (ej. acolchado agricola para dos hectareas), "
              f"basate en otras cotizaciones para realizar los calculos y en metododologias de calculo, proporcionadas en el contexto. "
              f"Si el usuario especifica un producto con detalles exactos (ej. modelo, capacidad, dimensiones), "
              f"solo incluye ese producto en la cotización, con descripcion y especificaciones detalladas y precio. "
              f"Usa los precios de la tienda online o de cotizaciones previas para determinar precios y especificaciones, "
              f"y solo si no hay referencias, deja el precio en blanco. Responde en español. "
              f"Nota: Los productos agricolas o insumos agricolas, equipo tecnico agricola, etc. No graban impuesto de IVA en mexico.\n\n"
              f"**Precios actuales en tienda online:**\n{price_context}\n"
              f"**Contexto adicional:**\n{context}\n\n"
              f"Producto a cotizar: {query}")

    response = llm.complete(prompt)
    return response.text
