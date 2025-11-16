from .embeddings import generate_embeddings
from .pinecone_setup import index
from .claude_llm_setup import llm
from models import Product, Supplier, SupplierProduct, SessionLocal

def get_products_from_db(fallback_margin=30.0):
    """
    Fetch products from supplier-product table and calculate prices with margin.
    Uses SupplierProduct.cost + SupplierProduct.default_margin to calculate final prices.
    Product table is deprecated - all pricing comes from SupplierProduct.
    
    Args:
        fallback_margin: Default margin percentage if default_margin is not set (default: 30%)
    """
    db = SessionLocal()
    try:
        # Query all active supplier products
        supplier_products = db.query(SupplierProduct).join(Product).join(Supplier).filter(
            SupplierProduct.is_active == True,
            Product.is_active == True,
            SupplierProduct.archived_at == None,
            Product.archived_at == None
        ).all()
        
        # Create compact product list
        product_lines = []
        for sp in supplier_products:
            product = sp.product
            supplier = sp.supplier
            
            # Calculate final price from supplier cost + margin
            if sp.cost:
                # Use product's default_margin, or fallback if not set
                margin_percentage = float(sp.default_margin) if sp.default_margin else fallback_margin
                
                # Calculate: cost * (1 + margin/100)
                # Example: $1000 * (1 + 30/100) = $1000 * 1.30 = $1300
                margin_multiplier = 1 + (margin_percentage / 100)
                final_price = float(sp.cost) * margin_multiplier
                
                price_str = f"${final_price:,.2f} MXN"
            else:
                # No cost available - can't calculate price
                price_str = "Consultar"
            
            # Format: Product | Supplier | Price (with margin applied) | Unit | SKU
            line = f"{product.name} | {supplier.name} | {price_str} | {product.unit.value} | SKU: {product.sku}"
            
            # Add specifications if available
            if product.specifications:
                specs = ", ".join([f"{k}: {v}" for k, v in product.specifications.items()])
                line += f" | {specs}"
                
            product_lines.append(line)
        
        return "\n".join(product_lines)
    finally:
        db.close()


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


def query_rag_system(query):
    """Generate a response using Shopify product search and historical context."""
    return query_rag_system_with_history(query, chat_history=None)


def query_rag_system_with_history(query, chat_history=None):
    """Generate a response using Shopify product search, historical context, and conversation history."""
    print(f'🔹 Query Received: {query}')
    
    if chat_history is None:
        chat_history = []
    
    # Generate query embedding for Pinecone context search
    query_embedding = generate_embeddings([query])[0]

    # Fetch relevant text context from Pinecone (historical quotations and catalog data)
    results = index.query(vector=query_embedding, top_k=7, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    # Step 1: Get products from database (replaces Shopify API)
    matched_products = get_products_from_db()
    
    # Step 2: Format chat history for prompt (last 4 messages)
    chat_history_text = ""
    if chat_history:
        chat_history_text = "\n\n**📝 Conversación previa:**\n"
        for msg in chat_history[-4:]:  # Last 4 messages (2 conversation turns)
            role = "Usuario" if msg["role"] == "user" else "Asistente"
            # Truncate long messages to avoid token bloat
            content = msg["content"][:500] + "..." if len(msg["content"]) > 500 else msg["content"]
            chat_history_text += f"\n{role}: {content}\n"
        chat_history_text += "\n"

    # Step 3: Construct the final prompt with conversation awareness
    prompt = (f"Genera una cotización detallada en formato markdown basada en el catálogo de productos, cotizaciones previas, "
      f"y características y precios de productos disponibles en el contexto. "
      f"Incluye especificaciones completas de los productos y precios disponibles, "
      f"considerando tanto los productos listados en la tienda online como aquellos que han sido cotizados previamente. "
      
      f"{chat_history_text}"
      
      f"📌 **IMPORTANTE - Manejo de Conversación:**\n"
      f"**Si existe una conversación previa:**\n"
      f"- Analiza TODA la conversación para entender el contexto completo\n"
      f"- Si el usuario proporciona información adicional (ej. 'cultivo de chile', 'son 2 hectáreas'), "
      f"REFINA la cotización anterior incorporando estos nuevos detalles\n"
      f"- NO generes una cotización completamente nueva si ya existe contexto previo relevante\n"
      f"- Mantén la estructura y productos mencionados anteriormente, pero ajusta con la nueva información\n"
      f"- Si el usuario cambia completamente de tema, entonces sí genera una nueva cotización\n\n"
      
      f"**Ejemplos de manejo de contexto:**\n"
      f"- Usuario: 'Cotización para acolchado agrícola' → Cotización general de acolchado\n"
      f"- Usuario: 'para cultivo de chile' → REFINAR cotización de acolchado específicamente para chile\n"
      f"- Usuario: 'son 2 hectáreas' → CALCULAR cantidades de acolchado para 2 hectáreas basado en el cultivo mencionado\n"
      f"- Usuario: 'ahora necesito charolas' → NUEVA cotización (cambio de tema)\n\n"
      
      f"📌 **Reglas para la cotización:**\n"
      f"1️⃣ **Si el usuario usa un término general** (ej. geomembranas, sistemas de riego, drones agrícolas), "
      f"genera varias opciones con diferentes modelos, especificaciones y precios.\n"
      
      f"2️⃣ **Si el usuario no especifica una variante (color, modelo, etc.),** "
      f"incluye **todas las opciones disponibles** en la cotización. "
      f"Ejemplo: si solicita 'acolchado 1.2m', muestra **negro/plata y negro/blanco** en lugar de solo la opción más barata.\n"
      
      f"3️⃣ **Si el usuario especifica un producto exacto** (modelo, capacidad, dimensiones, color, etc.), "
      f"incluye solo esa opción con su descripción, especificaciones y precio correspondiente.\n"
      
      f"4️⃣ **Usa tanto el catálogo de productos como las cotizaciones previas.** "
      f"Si un producto no aparece en el catálogo actual, pero ha sido cotizado previamente, usa esos datos históricos.\n"

      f"5️⃣ **Si el usuario proporciona datos técnicos para calcular un producto** (ej. acolchado agrícola para dos hectáreas), "
      f"usa las metodologías de cálculo y cotizaciones previas del contexto para estimar los productos y costos.\n"

      f"6️⃣ **Usa los precios más actualizados disponibles.** "
      f"Prioriza los precios del catálogo actual. Si no hay precio disponible, usa referencias de cotizaciones previas. "
      f"Si no hay referencia de precio en ninguna fuente, indica 'Consultar'.\n"

      f"📌 **Estructura esperada en la cotización:**\n"
      f"- Usa # para el título principal, ## para secciones principales, y ### para subsecciones. Asegúrate de incluir espacios después de los símbolos #.\n"
      f"- **Cálculos completos** (si aplica).\n"
      f"- **Especificaciones técnicas** detalladas de cada producto.\n"
      f"- **Tabla de precios** con cantidad, unidad y total, mostrando múltiples opciones (si aplica).\n"
      f"- **Notas importantes** sobre impuestos y recomendaciones.\n"
      f"- Usa saltos de línea simples entre elementos relacionados y dobles entre secciones principales.\n"

      f"📌 **FORMATO ESTRICTO DE TABLA:**\n"
      f"La tabla de precios DEBE usar EXACTAMENTE este formato de 5 columnas:\n"
      f"| Descripción | Unidad | Cantidad | Precio Unitario | Importe |\n"
      f"|:---|:---:|:---:|:---:|:---:|\n"
      f"| Nombre del producto | ROLLO/PIEZA/METRO | 28 | $2,250.00 MXN | $63,000.00 MXN |\n"
      f"\n"
      f"**Reglas críticas para la tabla:**\n"
      f"- SIEMPRE usar exactamente 5 columnas (no más, no menos)\n"
      f"- NO incluir columnas adicionales como 'Ancho', 'Largo por Rollo', etc. - esa info va en la Descripción\n"
      f"- Descripción: Incluir TODA la info del producto (nombre, ancho, color, especificaciones)\n"
      f"- Precio Unitario e Importe: SIEMPRE incluir el símbolo $ y MXN, ejemplo: $45,000.00 MXN\n"
      f"- Si no hay precio disponible, usar: 'Consultar' (sin símbolo $)\n"
      f"- Formato de números: Usar comas como separadores de miles\n"

      f"📌 **Formato del documento:**\n"
      f"Estructura el documento en este orden exacto:\n"
      f"1. Título (nombre del producto en mayúsculas)\n" 
      f"2. Especificaciones técnicas\n"
      f"3. Tabla de precios (usar formato de 5 columnas estricto)\n"
      f"4. Notas importantes\n"
      f"- Por favor usa **doble salto de línea** entre cada sección principal.\n"
      f"- Usa un único # para el título principal y limita su longitud a no más de 5 palabras.\n"

      f"📌 **Importante:**\n"
      f"- No asumas que un producto no existe solo porque no está en el catálogo actual. Verifica en cotizaciones previas.\n"
      f"- Prioriza siempre la información más reciente y relevante para la cotización.\n"
      f"- Responde en español.\n"
      f"- Asegúrate de que las tablas sean compatibles con markdown y tengan un formato adecuado.\n"

      f"📌 **Nota:** Los productos agrícolas, insumos agrícolas y equipo técnico agrícola están exentos de IVA en México.\n\n"
      
      f"**📄 Contexto adicional (productos previamente cotizados):**\n{context}\n\n"
      f"**📦 Catálogo de productos disponibles:**\n{matched_products}\n\n"
      
      f"**🔍 Consulta actual:** {query}")

    response = llm.complete(prompt)
    return response.text