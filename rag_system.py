from embeddings import generate_embeddings
from pinecone_setup import index
from shopify_products import fetch_shopify_prices
from claude_llm_setup import llm
from llama_index.core import Document, VectorStoreIndex
from llama_index.core.query_engine import RetrieverQueryEngine

# Initialize an in-memory LlamaIndex for price embeddings
price_index = None

def update_price_index():
    """Fetches latest Shopify prices and indexes them in memory with LlamaIndex (using embeddings)."""
    shopify_prices = fetch_shopify_prices()

    if shopify_prices:
        documents = [Document(text=f"{product}: ${price} MXN") for product, price in shopify_prices.items()]
        global price_index
        price_index = VectorStoreIndex.from_documents(documents)

    print("✅ Updated in-memory price index with Shopify prices (using embeddings).")


def query_rag_system(query):
    print(f'🔹 Query Received: {query}')

    # Generate query embedding
    query_embedding = generate_embeddings([query])[0]

    # Fetch top relevant text context from Pinecone
    results = index.query(vector=query_embedding, top_k=7, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    print(f'Context Fetched from Pinecone')

    # Fetch relevant price data using semantic search
    update_price_index()
    price_retriever = price_index.as_retriever()
    price_results = price_retriever.retrieve(query)
    price_context = "\n".join([str(result) for result in price_results])

    print(f'Price Context Retrieved from LlamaIndex: {price_context}')

    prompt = (f"Genera una cotización detallada basada en el catálogo de productos y cotizaciones previas. "
              f"Incluye especificaciones completas de los productos y precios disponibles. "
              
              f"- Si el usuario proporciona un término general (ej. geomembranas, sistemas de riego, drones agrícolas), "
              f"genera varias opciones con diferentes modelos, especificaciones y precios. "
              
              f"- Si el usuario proporciona datos técnicos para calcular los productos (ej. acolchado agrícola para dos hectáreas), "
              f"usa las metodologías de cálculo y cotizaciones previas disponibles en el contexto para estimar los productos y costos. "
              
              f"- Si el usuario especifica un producto exacto (ej. modelo, capacidad, dimensiones), "
              f"incluye solo ese producto con su descripción, especificaciones y precio correspondiente. "
              
              f"- Si múltiples productos o variantes coinciden con la solicitud, incluye todas las opciones relevantes en la cotización. "
              
              f"- Usa los precios de la tienda online o cotizaciones previas para calcular los costos. "
              f"Si no hay referencia de precio, deja el campo de precio en blanco. "
              
              f"Responde en español. "
              
              f"📌 **Nota:** Los productos agrícolas, insumos agrícolas y equipo técnico agrícola están exentos de IVA en México.\n\n"
              
              f"**📦 Precios actuales en tienda online:**\n{price_context}\n"
              f"**📄 Contexto adicional:**\n{context}\n\n"
              
              f"🔍 **Producto a cotizar:** {query}")

    # response = llm.complete(prompt)
    # return response.text
