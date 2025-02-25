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
          f"considerando tanto los productos listados en la tienda online como aquellos que han sido cotizados previamente. "
          
          f"📌 **Reglas para la cotización:**\n"
          f"1️⃣ **Si el usuario usa un término general** (ej. geomembranas, sistemas de riego, drones agrícolas), "
          f"genera varias opciones con diferentes modelos, especificaciones y precios.\n"
          
          f"2️⃣ **Si el usuario no especifica una variante (color, modelo, etc.),** "
          f"incluye **todas las opciones disponibles** en la cotización. "
          f"Ejemplo: si solicita 'acolchado 1.2m', muestra **negro/plata y negro/blanco** en lugar de solo la opción más barata.\n"
          
          f"3️⃣ **Si el usuario especifica un producto exacto** (modelo, capacidad, dimensiones, color, etc.), "
          f"incluye solo esa opción con su descripción, especificaciones y precio correspondiente.\n"
          
          f"4️⃣ **No te limites solo a los productos de la tienda online.** "
          f"Si un producto no aparece en la tienda online, pero ha sido cotizado previamente, usa esos datos para generar la cotización.\n"

          f"5️⃣ **Si el usuario proporciona datos técnicos para calcular un producto** (ej. acolchado agrícola para dos hectáreas), "
          f"usa las metodologías de cálculo y cotizaciones previas del contexto para estimar los productos y costos.\n"

          f"6️⃣ **Usa los precios más actualizados disponibles.** "
          f"Si hay precios en la tienda online y cotizaciones previas, prioriza la información más reciente. "
          f"Si no hay referencia de precio en ninguna fuente, deja el campo de precio en blanco.\n"

          f"📌 **Estructura esperada en la cotización:**\n"
          f"- **Cálculos completos** (si aplica).\n"
          f"- **Especificaciones técnicas** detalladas de cada producto.\n"
          f"- **Tabla de precios** con cantidad, unidad y total, mostrando múltiples opciones (si aplica).\n"
          f"- **Notas importantes** sobre impuestos y recomendaciones.\n"

          f"📌 **Importante:**\n"
          f"- No asumas que un producto no existe solo porque no está en la tienda online. Verifica en cotizaciones previas y el catálogo del contexto.\n"
          f"- Prioriza siempre la información más reciente y relevante para la cotización.\n"
          f"- Responde en español.\n"

          f"📌 **Nota:** Los productos agrícolas, insumos agrícolas y equipo técnico agrícola están exentos de IVA en México.\n\n"
          
          f"**📄 Contexto adicional (productos previamente cotizados o en catálogo):**\n{context}\n\n"
          f"**🛒 Precios actuales en tienda online:**\n{matched_products}\n"
          
          f"**🔍 Producto a cotizar:** {query}")


    response = llm.complete(prompt)
    return response.text