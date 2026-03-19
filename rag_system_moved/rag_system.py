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


def query_rag_system_with_history(query, chat_history=None, customer_name=None, customer_location=None):
    """Generate a response using database product search, historical context, and conversation history."""
    print(f'🔹 Query Received: {query}')
    
    if chat_history is None:
        chat_history = []
    
    # Generate query embedding for Pinecone context search
    query_embedding = generate_embeddings([query])[0]

    # Fetch relevant text context from Pinecone (historical quotations and catalog data)
    results = index.query(vector=query_embedding, top_k=7, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    # PHASE 1: Analyze and Calculate
    # We use the Pinecone context to help with the math/logic AND to extract dynamic notes
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

    # Step 3: Construct the final prompt with conversation awareness
    prompt = (f"Genera DOS cotizaciones en formato markdown basada en el REPORTE DE ANÁLISIS, los productos de la base de datos y cotizaciones previas. "
      f"Incluye especificaciones completas de los productos y precios disponibles.\n\n"
      
      f"🔥🔥 **INFORMACIÓN CRÍTICA - REPORTE DE ANÁLISIS Y CÁLCULO:** 🔥🔥\n"
      f"{calculation_report}\n"
      f"⚠️ **INSTRUCCIÓN 1 (CANTIDADES):** Usa las cantidades del reporte como la VERDAD TÉCNICA.\n"
      f"⚠️ **INSTRUCCIÓN 2 (NOTAS):** Usa las 'CONDICIONES COMERCIALES SUGERIDAS' del reporte para la sección de Notas. NO uses notas genéricas si el reporte sugiere otras específicas.\n\n"
      
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
      f"usa el REPORTE DE CÁLCULO para determinar las cantidades y luego las metodologías de cálculo y cotizaciones previas del contexto para estimar los productos y costos.\n"

      f"6️⃣ **Usa los precios más actualizados disponibles.** "
      f"Prioriza los precios del catálogo actual. Si no hay precio disponible, usa referencias de cotizaciones previas. "
      f"Si no hay referencia de precio en ninguna fuente, indica 'Consultar'.\n"

      f"📌 **Estructura esperada en la cotización:**\n"
      f"- Usa # para el título principal, ## para secciones principales, y ### para subsecciones. Asegúrate de incluir espacios después de los símbolos #.\n"
      f"- **Cálculos completos** (si aplica).\n"
      f"- **Especificaciones técnicas** detalladas de cada producto.\n"
      f"- **Tabla de precios** con cantidad, unidad y total, mostrando múltiples opciones (si aplica).\n"
      f"- **Notas importantes** (Dinámicas según el reporte).\n"
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
      
      f"**📋 FORMATO DE RESPUESTA - DEBES GENERAR DOS COTIZACIONES:**\n\n"
      f"Tu respuesta DEBE contener DOS cotizaciones separadas con el siguiente formato:\n\n"
      f"```\n"
      f"<!-- INTERNAL_QUOTATION_START -->\n"
      f"[COTIZACIÓN INTERNA DETALLADA AQUÍ]\n"
      f"<!-- INTERNAL_QUOTATION_END -->\n\n"
      f"<!-- CUSTOMER_QUOTATION_START -->\n"
      f"[COTIZACIÓN PARA CLIENTE AQUÍ]\n"
      f"<!-- CUSTOMER_QUOTATION_END -->\n"
      f"```\n\n"
      
      f"**COTIZACIÓN INTERNA (para uso interno de IMPAG):**\n"
      f"- Incluye TODOS los detalles: proveedor, costo unitario, margen aplicado, precio final\n"
      f"- **FUENTE DE DATOS:** Debes indicar explícitamente de dónde obtuviste la información de cada producto.\n"
      f"  * Si viene del catálogo actual, indica: 'Fuente: Base de Datos (ID: X)'\n"
      f"  * Si viene de una cotización histórica, indica: 'Fuente: Histórico (Cotización previa)'\n"
      f"- Incluye costos de instalación si aplican\n"
      f"- Incluye notas internas (ej: 'Contactar proveedor para precio actualizado', 'Verificar disponibilidad', etc.)\n"
      f"- Incluye información de envío y logística detallada\n"
      f"- Tabla con columnas: Descripción | Fuente | Proveedor | Costo Unitario | Margen | Precio Unitario | Cantidad | Importe\n"
      f"- Formato de tabla interna:\n"
      f"| Descripción | Fuente | Proveedor | Costo Unitario | Margen | Precio Unitario | Cantidad | Importe |\n"
      f"|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|\n"
      f"| Producto | Base de Datos | Proveedor ABC | $1,000.00 MXN | 30% | $1,300.00 MXN | 28 | $36,400.00 MXN |\n\n"
      
      f"**COTIZACIÓN PARA CLIENTE (lista para compartir):**\n"
      f"- Formato limpio y profesional, sin información interna\n"
      f"- NO incluir proveedor, costo unitario, ni margen\n"
      f"- Solo incluir: Descripción, Unidad, Cantidad, Precio Unitario, Importe\n"
      f"- En la columna CONCEPTO, incluir el nombre del producto en la primera línea.\n"
      f"- **IMPORTANTE:** NO incluir especificaciones técnicas detalladas en la cotización al cliente a menos que sean críticas para distinguir el producto (ej. dimensiones básicas). El cliente final no necesita saber detalles técnicos complejos.\n"
      f"- Formato de tabla cliente (5 columnas con encabezado azul):\n"
      f"| CONCEPTO | UNIDAD | CANTIDAD | P. UNITARIO | IMPORTE |\n"
      f"|:---|:---:|:---:|:---:|:---:|\n"
      f"| Nombre del producto (Dimensiones básicas) | ROLLO | 10 | $63,500.00 MXN | $635,000.00 MXN |\n"
      f"- Incluir fila de TOTAL con fondo azul al final de la tabla\n"
      f"\n"
      f"**ESTRUCTURA DESPUÉS DE LA TABLA:**\n"
      f"1. **Monto en palabras** (en una sola línea, sin viñetas):\n"
      f"   (SEISCIENTOS TREINTA Y CINCO MIL PESOS 00/100 MXN)\n"
      f"\n"
      f"2. **Sección de Notas** (usar ## Nota: como encabezado):\n"
      f"   - **IMPORTANTE:** Copia aquí las 'CONDICIONES COMERCIALES SUGERIDAS' del reporte de análisis.\n"
      f"   - Asegúrate de incluir la ubicación de entrega: {customer_location if customer_location else 'A convenir'}\n"
      f"\n"
      f"3. **Sección de Datos Bancarios** (usar ## DATOS BANCARIOS como encabezado):\n"
      f"   DATOS BANCARIOS IMPAG TECH SAPI DE C V\n"
      f"   BBVA BANCOMER\n"
      f"   CUENTA CLABE: 012 180 001193473561\n"
      f"   NUMERO DE CUENTA: 011 934 7356\n"
      f"\n"
      f"4. **Firma** (sin encabezado, solo el texto):\n"
      f"   Atentamente\n"
      f"   Juan Daniel Betancourt González\n"
      f"   Director de proyectos\n"
      f"\n"
      f"**REGLAS CRÍTICAS PARA EVITAR DUPLICACIÓN:**\n"
      f"- NO repetir el monto en palabras en la sección de Notas\n"
      f"- NO repetir los datos bancarios en la sección de Notas\n"
      f"- NO incluir la firma en la sección de Notas\n"
      f"- Cada sección debe aparecer UNA SOLA VEZ en el orden especificado\n"
      f"- Las notas deben ser SOLO las sugeridas en el reporte, nada más\n"
      f"\n"
      f"**FORMATO PARA PDF:**\n"
      f"- La tabla debe caber en una página A4 (210mm de ancho)\n"
      f"- Usar nombres de productos concisos en la columna CONCEPTO\n"
      f"- Si el nombre es muy largo, abreviarlo manteniendo claridad\n"
      
      f"**📄 Contexto adicional (productos previamente cotizados):**\n{context}\n\n"
      f"**📦 Catálogo de productos disponibles (para cliente):**\n{matched_products}\n\n"
      f"**📦 Catálogo de productos con detalles internos (para uso interno):**\n{matched_products_internal}\n\n"
      
      f"**👤 Información del Cliente:**\n"
      f"- Nombre: {customer_name if customer_name else 'A quien corresponda'}\n"
      f"- Ubicación: {customer_location if customer_location else 'No especificada'}\n\n"
      
      f"**🔍 Consulta actual:** {query}")

    response = llm.complete(prompt)
    return response.text