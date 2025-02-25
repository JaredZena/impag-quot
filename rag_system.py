from embeddings import generate_embeddings
from pinecone_setup import index
from shopify_products import fetch_shopify_prices
from claude_llm_setup import llm
from llama_index.core import Document, VectorStoreIndex

# Initialize global variables for product index and retriever
product_index = None
product_retriever = None

def update_product_index():
    """Fetch latest Shopify products, generate OpenAI embeddings, and store them in-memory."""
    global product_index, product_retriever

    shopify_prices = fetch_shopify_prices()

    if shopify_prices:
        # Create structured documents with detailed descriptions for better embeddings
        product_texts = [
            f"Producto: {product}\n"
            f"Descripción: {product} es un insumo agrícola utilizado para {get_category(product)}.\n"
            f"Precio: ${price} MXN"
            for product, price in shopify_prices.items()
        ]

        # Generate OpenAI embeddings for products
        product_embeddings = generate_embeddings(product_texts)

        # Create document objects with embeddings
        documents = [
            Document(text=product_text, metadata={"category": get_category(product)})
            for product_text, product in zip(product_texts, shopify_prices.keys())
        ]

        # Build an in-memory vector store for product search
        product_index = VectorStoreIndex.from_documents(documents)
        product_retriever = product_index.as_retriever(similarity_top_k=5)

    print("✅ Updated in-memory product index with Shopify product embeddings.")


def get_category(product_name):
    """Simple categorization function to improve product search results."""
    if "acolchado" in product_name.lower():
        return "Cobertura plástica agrícola"
    elif "malla sombra" in product_name.lower():
        return "Protección solar para cultivos"
    elif "trampa" in product_name.lower():
        return "Control de plagas"
    else:
        return "Otros insumos agrícolas"
    

def query_product_index(query):
    """Retrieve the most relevant product(s) based on the query using OpenAI embeddings."""
    if product_index is None or product_retriever is None:
        print("❌ Product index is empty. Please run `update_product_index()` first.")
        return None

    # Retrieve relevant products using OpenAI embeddings
    results = product_retriever.retrieve(query)

    if not results:
        print("❌ No relevant products found for query.")
        return None

    # Format the matched product details
    product_matches = "\n".join([str(result) for result in results])

    return product_matches


def query_rag_system(query):
    """Generate a response using Shopify product search and historical context."""
    print(f'🔹 Query Received: {query}')

    # Generate query embedding for Pinecone context search
    query_embedding = generate_embeddings([query])[0]

    # Fetch relevant text context from Pinecone (historical quotations and catalog data)
    results = index.query(vector=query_embedding, top_k=7, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    # Step 1: Ensure product index is up-to-date
    if product_index is None or product_retriever is None:
        update_product_index()

    # Step 2: Retrieve relevant Shopify products based on query
    matched_products = query_product_index(query)

    # Step 3: Construct the final prompt
    prompt = (f"Genera una cotización detallada basada en el catálogo de productos, cotizaciones previas, "
          f"y características y precios de productos disponibles en el contexto. "
          f"Incluye especificaciones completas de los productos y precios disponibles, "
          f"tomando en cuenta tanto los productos listados en la tienda online como aquellos que han sido cotizados previamente. "
          
          f"- Si el usuario proporciona un término general (ej. geomembranas, sistemas de riego, drones agrícolas), "
          f"genera varias opciones con diferentes modelos, especificaciones y precios. "
          
          f"- No te limites a los productos listados en la tienda online, también considera productos previamente cotizados "
          f"o registrados en el catálogo del contexto. Si un producto no aparece en la tienda online, "
          f"pero existen cotizaciones previas, usa esos datos para generar la cotización. "

          f"📌 **Asegúrate de incluir:**\n"
          f"1️⃣ **Cálculos completos** (si aplica).\n"
          f"2️⃣ **Especificaciones técnicas** de cada producto.\n"
          f"3️⃣ **Tabla de precios** con detalles de cantidad, unidad y total, incluyendo múltiples opciones de productos (si aplica).\n"
          f"4️⃣ **Notas importantes** sobre impuestos y recomendaciones.\n"
          
          f"- Si el usuario proporciona datos técnicos para calcular los productos (ej. acolchado agrícola para dos hectáreas), "
          f"usa las metodologías de cálculo y cotizaciones previas disponibles en el contexto para estimar los productos y costos. "
          
          f"- Si el usuario especifica un producto exacto (ej. modelo, capacidad, dimensiones, color, etc.), "
          f"incluye solo ese producto con su descripción, especificaciones y precio correspondiente. "
          
          f"- Si múltiples productos o variantes coinciden con la solicitud, incluye todas las opciones relevantes en la cotización. "
          
          f"- Usa los precios de la tienda online o cotizaciones previas para calcular los costos. "
          f"Si no hay referencia de precio ni en la tienda online ni en cotizaciones previas, deja el campo de precio en blanco. "

          f"La cotización debe incluir detalles técnicos, cantidades, precios unitarios, totales y notas adicionales en un formato estructurado. "
          
          f"📌 **Importante:**\n"
          f"- No asumas que un producto no existe si no está en la tienda online. Verifica en cotizaciones previas y el catálogo del contexto.\n"
          f"- Prioriza siempre la información más reciente y relevante para la cotización.\n"
          
          f"Responde en español. "
          
          f"**Nota:** Los productos agrícolas, insumos agrícolas y equipo técnico agrícola están exentos de IVA en México.\n\n"
          
          f"**📄 Contexto adicional (productos previamente cotizados o en catálogo):**\n{context}\n\n"
          f"**🛒 Precios actuales en tienda online:**\n{matched_products}\n"
          
          f"**🔍 Producto a cotizar:** {query}")

    response = llm.complete(prompt)
    return response.text