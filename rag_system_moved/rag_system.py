from .embeddings import generate_embeddings
from .pinecone_setup import index
from .claude_llm_setup import llm
from models import Product, Supplier, SupplierProduct, SessionLocal

def get_products_from_db(fallback_margin=30.0, include_internal_details=False):
    """
    Fetch products from supplier-product table and calculate prices with margin.
    Uses (SupplierProduct.cost + Shipping) + SupplierProduct.default_margin to calculate final prices.
    Product table is deprecated - all pricing comes from SupplierProduct.
    
    Args:
        fallback_margin: Default margin percentage if default_margin is not set (default: 30%)
        include_internal_details: If True, includes supplier cost and margin in the output
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
            
            # Calculate final price from supplier cost + shipping + margin
            if sp.cost:
                # Calculate shipping (Direct + Stages 1-4)
                shipping_total = (
                    float(sp.shipping_cost_direct or 0) + 
                    float(sp.shipping_stage1_cost or 0) + 
                    float(sp.shipping_stage2_cost or 0) + 
                    float(sp.shipping_stage3_cost or 0) + 
                    float(sp.shipping_stage4_cost or 0)
                )

                # Use product's default_margin (decimal: 0.25 = 25%), or fallback if not set
                margin_decimal = float(sp.default_margin) if sp.default_margin else (fallback_margin / 100)
                margin_percentage = margin_decimal * 100

                # Calculate Basis: Cost + Shipping
                cost_basis = float(sp.cost) + shipping_total

                # Standardized formula: price = cost / (1 - margin)
                final_price = cost_basis / (1 - margin_decimal) if margin_decimal < 1 else cost_basis
                
                price_str = f"${final_price:,.2f} MXN"
                
                # Include internal details if requested
                if include_internal_details:
                    cost_str = f"${float(sp.cost):,.2f} {sp.currency or 'MXN'}"
                    shipping_str = f"${shipping_total:,.2f}"
                    
                    shipping_warning = ""
                    if shipping_total == 0:
                        shipping_warning = " ⚠️ VERIFICAR ENVÍO (Costo $0.00)"
                    
                    margin_str = f"{margin_percentage:.1f}%"
                    line = f"{product.name} | {supplier.name} | Costo Base: {cost_str} | Envío: {shipping_str}{shipping_warning} | Costo Total: ${cost_basis:,.2f} | Margen: {margin_str} | Precio Final: {price_str} | {product.unit.value} | SKU: {product.sku}"
                else:
                    # Format: Product | Supplier | Price (with margin applied) | Unit | SKU
                    line = f"{product.name} | {supplier.name} | {price_str} | {product.unit.value} | SKU: {product.sku}"
            else:
                # No cost available - can't calculate price
                price_str = "Consultar"
                if include_internal_details:
                    line = f"{product.name} | {supplier.name} | Costo: Consultar | Margen: N/A | Precio Final: {price_str} | {product.unit.value} | SKU: {product.sku}"
                else:
                    line = f"{product.name} | {supplier.name} | {price_str} | {product.unit.value} | SKU: {product.sku}"
            
            # Add specifications if available
            if product.specifications:
                specs = ", ".join([f"{k}: {v}" for k, v in product.specifications.items()])
                line += f" | {specs}"
                
            product_lines.append(line)
        
        return "\n".join(product_lines)
    finally:
        db.close()

def get_relevant_products(query_embedding, limit=30, include_internal_details=False, fallback_margin=30.0):
    """
    Fetch relevant products using vector similarity search.
    Prioritizes database pricing (SupplierProduct). 
    """
    db = SessionLocal()
    try:
        # Semantic search using cosine distance
        supplier_products = db.query(SupplierProduct).join(Product).join(Supplier).filter(
            SupplierProduct.is_active == True,
            SupplierProduct.embedding != None
        ).order_by(
            SupplierProduct.embedding.cosine_distance(query_embedding)
        ).limit(limit).all()
        
        # Create compact product list
        product_lines = []
        for sp in supplier_products:
            product = sp.product
            supplier = sp.supplier
            
            # Calculate final price from supplier cost + shipping + margin
            if sp.cost:
                # Calculate shipping (Direct + Stages 1-4)
                shipping_total = (
                    float(sp.shipping_cost_direct or 0) + 
                    float(sp.shipping_stage1_cost or 0) + 
                    float(sp.shipping_stage2_cost or 0) + 
                    float(sp.shipping_stage3_cost or 0) + 
                    float(sp.shipping_stage4_cost or 0)
                )

                # Use product's default_margin, or fallback if not set
                # NOTE: default_margin is stored as DECIMAL (0.20 = 20%, not as percentage 20.00)
                margin_decimal = float(sp.default_margin) if sp.default_margin else (fallback_margin / 100)
                
                # Convert to percentage for display
                margin_percentage = margin_decimal * 100
                
                # IMPORTANT: Enforce minimum margin of 15% to prevent selling at cost
                MIN_MARGIN = 15.0
                margin_source = "DB"
                if margin_percentage < MIN_MARGIN:
                    margin_decimal = fallback_margin / 100
                    margin_percentage = fallback_margin
                    margin_source = f"FALLBACK (DB margin {float(sp.default_margin) * 100:.1f}% too low)"
                
                # Calculate Basis: Cost + Shipping
                cost_basis = float(sp.cost) + shipping_total

                # Standardized formula: price = cost / (1 - margin)
                final_price = cost_basis / (1 - margin_decimal) if margin_decimal < 1 else cost_basis
                
                price_str = f"${final_price:,.2f} MXN"
                
                # Include internal details if requested
                if include_internal_details:
                    cost_str = f"${float(sp.cost):,.2f} {sp.currency or 'MXN'}"
                    shipping_str = f"${shipping_total:,.2f}"
                    
                    shipping_warning = ""
                    if shipping_total == 0:
                        shipping_warning = " ⚠️ VERIFICAR ENVÍO (Costo $0.00)"

                    margin_str = f"{margin_percentage:.1f}% ({margin_source})"
                    # Internal view: Detailed specs + commercial info + SOURCE
                    specs_str = ""
                    if product.specifications:
                        specs_str = ", ".join([f"{k}: {v}" for k, v in product.specifications.items()])
                    
                    # Explicitly state source is DATABASE
                    line = f"SOURCE: DATABASE (SupplierProduct ID: {sp.id}) | {product.name} | {supplier.name} | Costo Base: {cost_str} | Envío: {shipping_str}{shipping_warning} | Costo Total: ${cost_basis:,.2f} | Margen: {margin_str} | Precio Final: {price_str} | {product.unit.value} | SKU: {product.sku} | Specs: {specs_str}"
                else:
                    # Customer view: Simplified for quotation generation
                    # We still provide specs to the AI so it can describe the product, 
                    # but we'll instruct the AI to be concise in the prompt.
                    line = f"PRODUCT: {product.name} | Precio: {price_str} | Unidad: {product.unit.value} | SKU: {product.sku}"
                    if product.specifications:
                        # Filter for key specs only for customer context if needed, 
                        # but usually better to give AI context and tell it to summarize.
                        specs = ", ".join([f"{k}: {v}" for k, v in product.specifications.items()])
                        line += f" | Specs: {specs}"
            else:
                # No cost available - fallback to "Consultar"
                price_str = "Consultar"
                if include_internal_details:
                    line = f"SOURCE: DATABASE (SupplierProduct ID: {sp.id}) | {product.name} | {supplier.name} | Costo: Consultar | Margen: N/A | Precio Final: {price_str} | {product.unit.value} | SKU: {product.sku}"
                else:
                    line = f"PRODUCT: {product.name} | Precio: {price_str} | Unidad: {product.unit.value} | SKU: {product.sku}"
            
            product_lines.append(line)
        
        if not product_lines:
            return "No matching products found in catalog."
            
        return "\n".join(product_lines)
    except Exception as e:
        print(f"Error in vector search: {e}")
        return "" # Return empty if fails, so we rely on Pinecone history
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
    """Generate a response using database product search and historical context."""
    return query_rag_system_with_history(query, chat_history=None)


def analyze_request_and_calculate(query, context):
    """
    Analyzes the user request to determine if a calculation is needed.
    If so, performs the calculation using the provided context (historical quotations).
    Also extracts relevant commercial conditions (notes) from historical context.
    """
    print(f'🔹 Analyzing Request & Calculating: {query}')
    
    analysis_prompt = (
        f"Actúa como un experto ingeniero agrónomo y matemático. Analiza la siguiente solicitud de cotización y el contexto histórico.\n\n"
        f"SOLICITUD DEL USUARIO: '{query}'\n\n"
        f"CONTEXTO HISTÓRICO (Cotizaciones previas y fórmulas):\n{context}\n\n"
        f"TU TAREA:\n"
        f"1. Determina si la solicitud requiere un CÁLCULO basado en dimensiones, área o uso.\n"
        f"2. Realiza los cálculos necesarios si aplican.\n"
        f"3. ANALIZA LAS NOTAS Y CONDICIONES de las cotizaciones históricas en el contexto. Identifica patrones para este tipo de producto (ej. tiempos de entrega específicos, condiciones de pago, maniobras).\n"
        f"4. Genera un set de 'NOTAS SUGERIDAS' dinámicas. No uses siempre las mismas. Adáptalas al producto. Por ejemplo, si es maquinaria, el tiempo de entrega suele ser mayor. Si son insumos, es menor.\n\n"
        
        f"FORMATO DE RESPUESTA:\n"
        f"--- REPORTE DE CÁLCULO ---\n"
        f"TIPO DE SOLICITUD: [CÁLCULO REQUERIDO / SOLICITUD DIRECTA]\n"
        f"ANÁLISIS: [Explica el análisis]\n"
        f"CÁLCULOS PASO A PASO:\n"
        f"[Matemáticas...]\n"
        f"RECOMENDACIÓN DE CANTIDADES:\n"
        f"- [Producto]: [Cantidad]\n"
        f"\n"
        f"--- CONDICIONES COMERCIALES SUGERIDAS ---\n"
        f"[Lista aquí las notas exactas que deben ir en la cotización. Incluye vigencia, pago, entrega, descarga, etc. Basado en lo que veas en el historial para productos similares.]\n"
        f"--------------------------\n"
    )
    
    response = llm.complete(analysis_prompt)
    return response.text


def _search_all_namespaces(query_embedding, customer_name=None, top_k=7):
    """
    Search across all relevant Pinecone namespaces for quotation context.
    Returns structured context from: historical quotations, WhatsApp conversations,
    past quotation documents, facturas, and catalogs.
    """
    from services.pinecone_service import search_vectors

    # Namespaces to search, ordered by relevance for quotation generation
    namespaces = [
        '',                    # Original historical quotations (legacy)
        'cotizaciones',        # Quotation PDFs/DOCX from WhatsApp
        'whatsapp-chats',      # WhatsApp conversation context (pricing discussions, customer interactions)
        'catalogos',           # Product catalogs
        'facturas',            # Invoices (real pricing data)
        'notas',               # Sales notes
    ]

    results = search_vectors(
        query_embedding=query_embedding,
        namespaces=namespaces,
        top_k=top_k,
    )

    # Organize by source type for the prompt
    historical_context = []
    whatsapp_context = []
    document_context = []

    for r in results:
        text = r.get("metadata", {}).get("text", "")
        ns = r.get("namespace", "")
        score = r.get("score", 0)
        filename = r.get("metadata", {}).get("original_filename", "")

        if not text:
            continue

        # If customer name is provided, boost results that mention them
        entry = {"text": text, "score": score, "source": ns, "filename": filename}

        if ns == "whatsapp-chats":
            whatsapp_context.append(entry)
        elif ns in ("", "general", "supplier-quotations", "customer-quotations"):
            historical_context.append(entry)
        else:
            document_context.append(entry)

    # If customer name provided, search WhatsApp specifically for their conversations
    if customer_name:
        customer_query = f"conversación con {customer_name}"
        from rag_system_moved.embeddings import generate_embeddings as gen_emb
        customer_embedding = gen_emb([customer_query])[0]
        customer_results = search_vectors(
            query_embedding=customer_embedding,
            namespaces=["whatsapp-chats"],
            top_k=5,
        )
        for r in customer_results:
            text = r.get("metadata", {}).get("text", "")
            if text and customer_name.lower() in text.lower():
                whatsapp_context.insert(0, {
                    "text": text, "score": r.get("score", 0),
                    "source": "whatsapp-chats", "filename": "",
                })

    # Build structured context string
    context_parts = []

    if historical_context:
        lines = [e["text"] for e in historical_context[:top_k]]
        context_parts.append("**Cotizaciones históricas:**\n" + "\n---\n".join(lines))

    if whatsapp_context:
        lines = [e["text"] for e in whatsapp_context[:5]]
        context_parts.append("**Conversaciones WhatsApp relevantes:**\n" + "\n---\n".join(lines))

    if document_context:
        lines = []
        for e in document_context[:5]:
            src = e.get("filename", e["source"])
            lines.append(f"[{src}] {e['text']}")
        context_parts.append("**Documentos relacionados (facturas, catálogos, notas):**\n" + "\n---\n".join(lines))

    combined = "\n\n".join(context_parts)

    print(f"🔹 Context sources: {len(historical_context)} historical, {len(whatsapp_context)} whatsapp, {len(document_context)} documents")

    return combined


def query_rag_system_with_history(query, chat_history=None, customer_name=None, customer_location=None):
    """Generate a response using database product search, historical context, and conversation history."""
    print(f'🔹 Query Received: {query}')

    if chat_history is None:
        chat_history = []

    # Generate query embedding for Pinecone context search
    query_embedding = generate_embeddings([query])[0]

    # Search across ALL namespaces: historical quotations, WhatsApp conversations,
    # quotation documents, facturas, catalogs
    context = _search_all_namespaces(query_embedding, customer_name=customer_name)

    # PHASE 1: Analyze and Calculate
    # We use the full context (including WhatsApp + documents) for math/logic AND dynamic notes
    calculation_report = analyze_request_and_calculate(query, context)
    print(f"🔹 Calculation & Analysis Report:\n{calculation_report}")

    # Step 1: Get products from database using semantic search
    matched_products = get_relevant_products(query_embedding)
    matched_products_internal = get_relevant_products(query_embedding, include_internal_details=True)
    
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

    # Shared preamble used by both calls
    shared_context = (
        f"🔥🔥 **REPORTE DE ANÁLISIS Y CÁLCULO:** 🔥🔥\n"
        f"{calculation_report}\n"
        f"⚠️ Usa las cantidades del reporte como VERDAD TÉCNICA.\n"
        f"⚠️ Usa las 'CONDICIONES COMERCIALES SUGERIDAS' del reporte para la sección de Notas.\n\n"
        f"{chat_history_text}"
        f"📌 **Manejo de conversación previa:**\n"
        f"- Si hay contexto previo, REFINA la cotización incorporando nuevos detalles\n"
        f"- Solo genera una cotización nueva si el usuario cambia completamente de tema\n\n"
        f"📌 **Reglas de productos:**\n"
        f"1. Término general → muestra todas las variantes disponibles\n"
        f"2. Sin variante especificada → incluye todas las opciones (ej. negro/plata y negro/blanco)\n"
        f"3. Producto exacto → solo esa opción\n"
        f"4. Si no está en catálogo, usa cotizaciones previas\n"
        f"5. Usa REPORTE DE CÁLCULO para cantidades cuando hay datos técnicos\n"
        f"6. Precios: catálogo actual > cotizaciones previas > WhatsApp > 'Consultar'\n\n"
        f"📌 **Fuentes (orden de prioridad):**\n"
        f"1. Catálogo actual (base de datos)\n"
        f"2. Cotizaciones previas (COT-IMPAG)\n"
        f"3. Conversaciones WhatsApp\n"
        f"4. Facturas/CFDIs (SOLO interno, NUNCA al cliente)\n"
        f"5. Catálogos de proveedores\n\n"
        f"📌 Productos agrícolas e insumos están exentos de IVA en México. Responde en español.\n\n"
        f"**📄 Contexto (cotizaciones previas, WhatsApp, documentos):**\n{context}\n\n"
        f"**👤 Cliente:** {customer_name if customer_name else 'A quien corresponda'} | "
        f"Ubicación: {customer_location if customer_location else 'A convenir'}\n\n"
        f"**🔍 Consulta:** {query}"
    )

    table_rules = (
        f"📌 **FORMATO ESTRICTO DE TABLA (5 columnas):**\n"
        f"| Descripción | Unidad | Cantidad | Precio Unitario | Importe |\n"
        f"|:---|:---:|:---:|:---:|:---:|\n"
        f"| Nombre del producto (especificaciones) | ROLLO | 28 | $2,250.00 MXN | $63,000.00 MXN |\n"
        f"- SIEMPRE 5 columnas. Info extra del producto va en Descripción, no en columnas extra.\n"
        f"- Precio Unitario e Importe: incluir $ y MXN. Sin precio: 'Consultar'.\n"
        f"- Números con comas como separadores de miles.\n"
        f"- Doble salto de línea entre secciones principales.\n"
    )

    # ── CALL 1: INTERNAL QUOTATION ──────────────────────────────────────────
    internal_prompt = (
        f"Genera la COTIZACIÓN INTERNA de IMPAG en formato markdown.\n\n"
        f"{shared_context}\n\n"
        f"**📦 Catálogo con detalles internos:**\n{matched_products_internal}\n\n"
        f"{table_rules}\n"
        f"**COTIZACIÓN INTERNA — incluye:**\n"
        f"- Proveedor, costo unitario, margen aplicado, precio final por producto\n"
        f"- Fuente de cada precio: 'Fuente: Base de Datos (ID: X)' o 'Fuente: Histórico'\n"
        f"- Costos de instalación y logística si aplican\n"
        f"- Notas internas (verificar disponibilidad, contactar proveedor, etc.)\n"
        f"- Tabla de 8 columnas:\n"
        f"| Descripción | Fuente | Proveedor | Costo Unitario | Margen | Precio Unitario | Cantidad | Importe |\n"
        f"|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|\n"
        f"| Producto | Base de Datos | Proveedor ABC | $1,000.00 MXN | 30% | $1,300.00 MXN | 28 | $36,400.00 MXN |\n"
    )

    print("🔹 Generating internal quotation...")
    internal_response = llm.complete(internal_prompt)
    internal_quotation = internal_response.text

    # ── CALL 2: CUSTOMER QUOTATION ──────────────────────────────────────────
    customer_prompt = (
        f"Genera la COTIZACIÓN PARA CLIENTE de IMPAG en formato markdown limpio y profesional.\n\n"
        f"{shared_context}\n\n"
        f"**📦 Catálogo de productos:**\n{matched_products}\n\n"
        f"{table_rules}\n"
        f"**COTIZACIÓN CLIENTE — reglas:**\n"
        f"- NO incluir proveedor, costo unitario ni margen\n"
        f"- NO incluir especificaciones técnicas detalladas (solo dimensiones básicas si son necesarias)\n"
        f"- Tabla de 5 columnas:\n"
        f"| CONCEPTO | UNIDAD | CANTIDAD | P. UNITARIO | IMPORTE |\n"
        f"|:---|:---:|:---:|:---:|:---:|\n"
        f"| Nombre del producto (dimensiones básicas) | ROLLO | 10 | $63,500.00 MXN | $635,000.00 MXN |\n"
        f"- Incluir fila TOTAL al final de la tabla\n\n"
        f"**ESTRUCTURA DESPUÉS DE LA TABLA (en este orden exacto, cada sección UNA SOLA VEZ):**\n"
        f"1. Monto en palabras (una línea): (SEISCIENTOS TREINTA Y CINCO MIL PESOS 00/100 MXN)\n"
        f"2. ## Nota: — copia las 'CONDICIONES COMERCIALES SUGERIDAS' del reporte. Incluye ubicación: {customer_location if customer_location else 'A convenir'}\n"
        f"3. ## DATOS BANCARIOS\n"
        f"   DATOS BANCARIOS IMPAG TECH SAPI DE C V\n"
        f"   BBVA BANCOMER\n"
        f"   CUENTA CLABE: 012 180 001193473561\n"
        f"   NUMERO DE CUENTA: 011 934 7356\n"
        f"4. Firma (sin encabezado):\n"
        f"   Atentamente\n"
        f"   Juan Daniel Betancourt González\n"
        f"   Director de proyectos\n"
    )

    print("🔹 Generating customer quotation...")
    customer_response = llm.complete(customer_prompt)
    customer_quotation = customer_response.text

    # Return in same format as before so frontend parsing is unchanged
    return (
        f"<!-- INTERNAL_QUOTATION_START -->\n"
        f"{internal_quotation}\n"
        f"<!-- INTERNAL_QUOTATION_END -->\n\n"
        f"<!-- CUSTOMER_QUOTATION_START -->\n"
        f"{customer_quotation}\n"
        f"<!-- CUSTOMER_QUOTATION_END -->"
    )