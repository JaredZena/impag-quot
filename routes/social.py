from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import anthropic
import os
import json
import random
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import get_db, Product, ProductCategory, SocialPost
from auth import verify_google_token

router = APIRouter()

claude_api_key = os.getenv("CLAUDE_API_KEY")

# --- Configuration Constants (Moved from Frontend) ---

CONTACT_INFO = {
    "web": "todoparaelcampo.com.mx",
    "whatsapp": "677-119-7737",
    "location": "Nuevo Ideal, Durango",
    "social": "@impag.tech",
    "email": "ventas@impag.tech"
}

SEASON_PATTERNS = {
    1: {"phase": "germinacion", "name": "Enero", "actions": ["Venta sustratos", "Charolas germinación", "Semillas inicio"]},
    2: {"phase": "trasplante", "name": "Febrero", "actions": ["Preparación suelo", "Sistemas riego", "Acolchados"]},
    3: {"phase": "crecimiento", "name": "Marzo", "actions": ["Fertilizantes inicio", "Protección plagas", "Tutoreo"]},
    4: {"phase": "crecimiento", "name": "Abril", "actions": ["Fertirriego", "Bioestimulantes", "Control preventivo"]},
    5: {"phase": "cosecha-temprana", "name": "Mayo", "actions": ["Herramientas cosecha", "Empaque", "Logística"]},
    6: {"phase": "cosecha-alta", "name": "Junio", "actions": ["Cajas plásticas", "Tarimas", "Mantenimiento post-cosecha"]},
    7: {"phase": "lluvias", "name": "Julio", "actions": ["Fungicidas", "Drenaje", "Protección humedad"]},
    8: {"phase": "pre-ciclo-oi", "name": "Agosto", "actions": ["Limpieza terreno", "Desinfección", "Planeación O-I"]},
    9: {"phase": "siembra-oi", "name": "Septiembre", "actions": ["Semilla cebolla/ajo", "Cinta riego", "Acolchado"]},
    10: {"phase": "desarrollo-oi", "name": "Octubre", "actions": ["Nutrición foliar", "Enraizadores", "Monitoreo"]},
    11: {"phase": "frio-temprano", "name": "Noviembre", "actions": ["Manta térmica", "Anti-heladas", "Invernaderos"]},
    12: {"phase": "proteccion-frio", "name": "Diciembre", "actions": ["Calefacción", "Sellado invernadero", "Mantenimiento"]}
}

DEFAULT_DATES = [
    {"month": 12, "day": 12, "name": "Día de la Virgen (México)"},
    {"month": 12, "day": 24, "name": "Nochebuena"},
    {"month": 12, "day": 31, "name": "Fin de Año"},
    {"month": 1, "day": 1, "name": "Año Nuevo"},
    {"month": 2, "day": 14, "name": "San Valentín (Floricultura)"},
    {"month": 5, "day": 10, "name": "Día de las Madres (Floricultura)"},
    {"month": 5, "day": 15, "name": "Día del Agricultor"},
]

POST_TYPES_DEFINITIONS = """
- Infografías: Explicar rápido (riego, acolchado). Versión resumida para Reels.
- Fechas importantes: Anclar promos o recordatorios (Día del Agricultor, heladas).
- Memes/tips rápidos: Humor educativo (errores comunes).
- Promoción puntual: Liquidar overstock o empujar alta rotación.
- Kits: Combo de productos (solución completa, ej. kit riego).
- Caso de éxito / UGC: Prueba social (instalaciones, resultados).
- Antes / Después: Demostrar impacto visual.
- Checklist operativo: Guía de acciones por temporada (previo a helada, arranque riego).
- Tutorial corto / "Cómo se hace": Educar en 30–45s.
- "Lo que llegó hoy": Novedades y entradas de inventario.
- FAQ / Mitos: Remover objeciones (costos, duración).
- Seguridad y prevención: Cuidado de personal/equipo.
- ROI / números rápidos: Justificar inversión con datos.
- Convocatoria a UGC: Pedir fotos/video de clientes.
- Recordatorio de servicio: Mantenimiento (lavado filtros, revisión bomba).
- Cómo pedir / logística: Simplificar proceso de compra.
"""

# --- Models ---

class SocialGenRequest(BaseModel):
    date: str # YYYY-MM-DD
    # Optional overrides allow testing specific scenarios, but defaults are autonomous
    category: Optional[str] = None

class SocialGenResponse(BaseModel):
    caption: str
    image_prompt: str
    posting_time: Optional[str] = None
    notes: Optional[str] = None
    format: Optional[str] = None
    cta: Optional[str] = None
    selected_product_id: Optional[str] = None
    selected_category: Optional[str] = None # New field for AI decision
    selected_product_details: Optional[Dict[str, Any]] = None # Full product object for frontend

# --- Logic ---

def repair_json_string(json_str: str) -> str:
    """
    Attempt to repair common JSON issues like unterminated strings.
    Simple approach: find and close any unterminated strings.
    """
    # Count quotes (ignoring escaped quotes)
    # If we have an odd number, there's an unterminated string
    quote_count = 0
    escape_next = False
    
    for char in json_str:
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"':
            quote_count += 1
    
    # If odd number of quotes, try to close the last string
    if quote_count % 2 == 1:
        # Find the last opening quote that might be unterminated
        # Work backwards to find the last " that's not part of a key
        last_quote_pos = json_str.rfind('"')
        if last_quote_pos != -1:
            # Check what comes after this quote
            after_quote = json_str[last_quote_pos + 1:]
            # If there's no closing quote, comma, or brace after it (ignoring whitespace),
            # we need to close it
            after_quote_clean = after_quote.strip()
            if after_quote_clean and not (after_quote_clean.startswith(',') or 
                                         after_quote_clean.startswith('}') or
                                         after_quote_clean.startswith('"') or
                                         after_quote_clean.startswith('\n')):
                # Find where to insert the closing quote (before the last } or ,)
                last_brace = json_str.rfind('}')
                if last_brace > last_quote_pos:
                    # Insert closing quote before the brace
                    json_str = json_str[:last_brace] + '"' + json_str[last_brace:]
                else:
                    # Just append closing quote
                    json_str = json_str + '"'
    
    return json_str


def clean_json_text(text: str) -> str:
    """
    Extract JSON from text that may contain markdown code blocks or extra text.
    Handles cases where JSON is wrapped in ```json``` or has text before/after.
    Also attempts to repair common JSON issues.
    """
    text = text.strip()
    
    # Remove markdown code blocks
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    
    text = text.strip()
    
    # Find the first complete JSON object by matching braces
    brace_count = 0
    start_idx = -1
    end_idx = -1
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\':
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if not in_string:
            if char == '{':
                if start_idx == -1:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    end_idx = i + 1
                    break
    
    # If we found a complete JSON object, extract and validate it
    if start_idx != -1 and end_idx != -1:
        json_str = text[start_idx:end_idx]
        try:
            # Validate it's actually valid JSON
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            # Try to repair the JSON
            try:
                repaired = repair_json_string(json_str)
                # Validate the repaired version
                json.loads(repaired)
                return repaired
            except:
                # If repair fails, try a simpler approach: close any unterminated strings
                # Find the last unclosed quote and close it before the closing brace
                if '"' in json_str:
                    # Count quotes - if odd, we have an unterminated string
                    quote_count = json_str.count('"') - json_str.count('\\"')
                    if quote_count % 2 == 1:
                        # Find the last opening quote that's not closed
                        last_quote_idx = json_str.rfind('"')
                        if last_quote_idx != -1:
                            # Check if there's a closing quote after this
                            remaining = json_str[last_quote_idx+1:]
                            if '"' not in remaining.replace('\\"', ''):
                                # No closing quote found, add one before the last }
                                last_brace = json_str.rfind('}')
                                if last_brace != -1:
                                    json_str = json_str[:last_brace] + '"' + json_str[last_brace:]
                                    try:
                                        json.loads(json_str)
                                        return json_str
                                    except:
                                        pass
                # If all else fails, return the original and let the caller handle it
                pass
    
    # Fallback: try to parse the whole text as-is
    return text

def get_season_context(date_obj):
    month = date_obj.month
    return SEASON_PATTERNS.get(month, SEASON_PATTERNS[1])

def get_nearby_dates(date_obj):
    # Simple logic: return dates in the same month
    month = date_obj.month
    return [d for d in DEFAULT_DATES if d["month"] == month]

def fetch_db_products(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch random active products from the database.
    """
    db_products = db.query(Product).join(ProductCategory).filter(
        Product.is_active == True
    ).limit(limit).all()
    
    catalog = []
    for p in db_products:
        cat_name = p.category.name if p.category else "General"
        catalog.append({
            "id": str(p.id),
            "name": p.name,
            "category": cat_name,
            "inStock": p.stock > 0 if p.stock is not None else False,
            "sku": p.sku
        })
    return catalog

def search_products(db: Session, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search products by name/description using simple ILIKE matching.
    """
    if not query:
        return fetch_db_products(db, limit) # Fallback to random if no query

    # Split keywords for better matching (e.g. "manta termica" -> %manta% AND %termica%)
    terms = query.split()
    filters = []
    for term in terms:
        filters.append(Product.name.ilike(f"%{term}%"))
    
    # Search products (active only)
    # matching ALL terms (AND logic) for precision, or OR for recall?
    # Let's try OR for recall, then rank? Or just Name match.
    # Simple approach: Name matches ANY term OR Description matches ANY term
    
    db_products = db.query(Product).join(ProductCategory).filter(
        Product.is_active == True,
        Product.name.ilike(f"%{query}%") # Try exact phrase match first or simple contain
    ).limit(limit).all()
    
    # If loose match needed:
    if not db_products and len(terms) > 0:
         # Fallback: search by first word
         db_products = db.query(Product).join(ProductCategory).filter(
            Product.is_active == True,
            Product.name.ilike(f"%{terms[0]}%")
        ).limit(limit).all()

    catalog = []
    for p in db_products:
        cat_name = p.category.name if p.category else "General"
        catalog.append({
            "id": str(p.id),
            "name": p.name,
            "category": cat_name,
            "inStock": p.stock > 0 if p.stock is not None else False,
            "sku": p.sku
        })
    return catalog

class SocialPostSaveRequest(BaseModel):
    date_for: str
    caption: str
    image_prompt: Optional[str] = None
    post_type: Optional[str] = None
    status: str = "planned"
    selected_product_id: Optional[str] = None
    formatted_content: Optional[Dict[str, Any]] = None

@router.post("/save")
async def save_social_post(
    payload: SocialPostSaveRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """Save a generated/approved post to the backend history."""
    try:
        new_post = SocialPost(
            date_for=payload.date_for,
            caption=payload.caption,
            image_prompt=payload.image_prompt,
            post_type=payload.post_type,
            status=payload.status,
            selected_product_id=payload.selected_product_id,
            formatted_content=payload.formatted_content
        )
        db.add(new_post)
        db.commit()
        db.refresh(new_post)
        return {"status": "success", "id": new_post.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate", response_model=SocialGenResponse)
async def generate_social_copy(
    payload: SocialGenRequest,
    db: Session = Depends(get_db)
):
    """
    Agentic Generation Workflow with DB History.
    """
    if not claude_api_key:
        raise HTTPException(status_code=500, detail="CLAUDE_API_KEY not configured")

    client = anthropic.Client(api_key=claude_api_key)

    # --- 0. FETCH HISTORY (BACKEND) ---
    # Fetch last 15 posts to avoid repetition
    recent_posts = db.query(SocialPost).order_by(SocialPost.created_at.desc()).limit(15).all()
    history_items = [f"{p.caption[:60]}... (Type: {p.post_type})" for p in recent_posts]
    recent_history = "\n- ".join(history_items)

    # --- 1. CONTEXT INIT ---
    try:
        dt = datetime.strptime(payload.date, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()

    sales_context = get_season_context(dt)
    important_dates = str([d["name"] for d in get_nearby_dates(dt)])
    
    # --- 2. STRATEGY PHASE ---
    strategy_prompt = (
        f"ACTÚA COMO: Director de Estrategia Comercial. FECHA: {payload.date}\n"
        f"FASE AGRÍCOLA: {sales_context['phase']} ({sales_context['name']}).\n"
        f"ACCIONES SUGERIDAS: {', '.join(sales_context['actions'])}.\n"
        f"EFEMÉRIDES: {important_dates}.\n"
        f"PREFERENCIA USUARIO: {payload.category or 'Ninguna (Decide tú)'}.\n\n"
        
        "HISTORIAL RECIENTE (EVITA REPETIR ESTOS TEMAS):\n"
        f"- {recent_history or 'Sin historial previo.'}\n\n"

        "TIPOS DE POST DISPONIBLES:\n"
        f"{POST_TYPES_DEFINITIONS}\n\n"

        "TU TAREA: Decide el TEMA del post de hoy y si necesitamos buscar productos.\n"
        "RESPONDE SOLO JSON:\n"
        "{\n"
        '  "topic": "Tema principal (ej. Preparación de suelo)",\n'
        '  "post_type": "promo/educativo/meme",\n'
        '  "search_needed": true/false,\n'
        '  "search_keywords": "términos de búsqueda para base de datos (ej. arado, fertilizante inicio)"\n'
        "}"
    )
    
    try:
        strat_resp = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=300,
            temperature=0.5,
            system="Eres un cerebro estratégico. Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional antes o después. No incluyas explicaciones, solo el JSON.",
            messages=[{"role": "user", "content": strategy_prompt}]
        )
        response_text = strat_resp.content[0].text
        cleaned_json = clean_json_text(response_text)
        strat_data = json.loads(cleaned_json)
    except json.JSONDecodeError as e:
        print(f"Strategy JSON Parse Error: {e}")
        print(f"Response text: {strat_resp.content[0].text[:200] if 'strat_resp' in locals() else 'No response'}")
        # Fallback Strategy
        strat_data = {"topic": "General", "search_needed": True, "search_keywords": ""}
    except Exception as e:
        print(f"Strategy Error: {e}")
        # Fallback Strategy
        strat_data = {"topic": "General", "search_needed": True, "search_keywords": ""}

    # --- 3. RETRIEVAL PHASE ---
    found_products = []
    if strat_data.get("search_needed"):
        keywords = strat_data.get("search_keywords", "")
        # If user overrode category, prioritize that in search? No, rely on keywords.
        found_products = search_products(db, keywords, limit=15)
        
        # If search yielded nothing, fallback to random active products
        if not found_products:
            found_products = fetch_db_products(db, limit=10)
    
    catalog_str = "\n".join([
        f"- {p['name']} (Cat: {p['category']}, Stock: {'✅' if p['inStock'] else '⚠️'}, ID: {p['id']})"
        for p in found_products
    ])

    # --- 4. CREATION PHASE ---
    brand_guardrails = (
        "Marca IMPAG. Paleta blanco/gris con acentos verde/azul. "
        f"Contacto: {CONTACT_INFO['whatsapp']}."
    )

    # Build product details for image prompt if product is selected
    selected_product_info = ""
    if found_products:
        # Get first product details for context
        first_product = found_products[0]
        selected_product_info = f"\nProducto seleccionado: {first_product['name']} (Categoría: {first_product['category']}, SKU: {first_product['sku']})"

    creation_prompt = (
        f"ACTÚA COMO: Social Media Manager. TEMA ELEGIDO: {strat_data.get('topic')}\n"
        f"TIPO DE POST: {strat_data.get('post_type')}\n"
        f"{selected_product_info}\n\n"
        
        "PRODUCTOS ENCONTRADOS EN ALMACÉN (Usa uno si aplica):\n"
        f"{catalog_str}\n\n"
        
        "INSTRUCCIONES:\n"
        "1. Selecciona el mejor producto de la lista (si hay) que encaje con el tema.\n"
        "2. Si no hay productos relevantes, haz un post genérico de marca/educativo.\n"
        "3. Prioriza productos con Stock ✅.\n"
        "4. Genera el contenido.\n\n"
        
        "--- INSTRUCCIONES ESPECÍFICAS PARA image_prompt ---\n"
        "El campo 'image_prompt' DEBE ser un prompt detallado y técnico para generación de imágenes (estilo IMPAG).\n"
        "Sigue este formato estructurado:\n\n"
        
        "FORMATO REQUERIDO:\n"
        "Genera una imagen cuadrada 1080×1080 px, estilo [flyer técnico/paisaje agrícola/catálogo técnico] IMPAG, con diseño limpio, moderno y profesional.\n"
        "Mantén siempre la estética corporativa IMPAG: fondo agrícola difuminado, tonos blanco–gris, acentos verde–azul, sombras suaves, tipografías gruesas para títulos y delgadas para texto técnico.\n\n"
        
        "Instrucciones de diseño detalladas:\n"
        "1. LOGO IMPAG:\n"
        "   - Colocar el logo 'IMPAG Agricultura Inteligente' en la esquina superior derecha, sin deformarlo y manteniendo la proporción.\n"
        "   - O mencionar 'espacio reservado para logo IMPAG en esquina superior derecha'.\n\n"
        
        "2. ELEMENTO PRINCIPAL:\n"
        "   - Si hay producto: Imagen realista del producto en alta resolución, fotorealista, iluminación de estudio suave o golden hour.\n"
        "   - Si es paisaje: Paisaje agrícola realista del norte de México (Durango), cultivos en hileras, iluminación natural suave.\n"
        "   - Si es kit: Componentes completamente visibles, montados o desglosados en técnica 'knolling', cables ordenados.\n"
        "   - Mantener proporción, ubicación, integración suave con fondo, estilo profesional tipo catálogo.\n\n"
        
        "3. ESPECIFICACIONES TÉCNICAS (si aplica):\n"
        "   - Bloque técnico con viñetas: 📏 Especificaciones Técnicas:\n"
        "   - Lista de 4-6 datos técnicos relevantes del producto\n"
        "   - Respetar viñetas, colores, alineación, tipografía, fondo del recuadro y sombra.\n\n"
        
        "4. PIE DEL FLYER (mantener estilo IMPAG):\n"
        f"   - {CONTACT_INFO['web']}\n"
        "   - Envíos a todo México\n"
        f"   - WhatsApp: {CONTACT_INFO['whatsapp']}\n"
        f"   - 📍 {CONTACT_INFO['location']}\n\n"
        
        "5. ESTILO GENERAL:\n"
        "   - Flyer técnico–comercial IMPAG, moderno, limpio\n"
        "   - Fuerte presencia visual del producto o tema\n"
        "   - Enfoque agrícola profesional y composición integrada\n"
        "   - Fotografía realista, NO estilo cartoon o ilustración\n"
        "   - Alta definición, colores vibrantes pero naturales\n"
        "   - Iluminación: Golden hour, amanecer, o estudio suave\n"
        "   - Profundidad de campo controlada (bokeh en fondos si aplica)\n\n"
        
        "EJEMPLOS DE PROMPTS CORRECTOS:\n"
        "- 'Genera una imagen cuadrada 1080×1080 px, estilo flyer técnico IMPAG, con diseño limpio, moderno y profesional. Mantén siempre la estética corporativa IMPAG: fondo agrícola difuminado, tonos blanco–gris, acentos verde–azul, sombras suaves, tipografías gruesas para títulos y delgadas para texto técnico. Logo IMPAG en esquina superior derecha. Imagen realista del producto [nombre] en alta resolución, fotorealista, iluminación de estudio suave. Bloque técnico con especificaciones: [lista de specs]. Pie del flyer: todoparaelcampo.com.mx, Envíos a todo México, WhatsApp: 677-119-7737. Estilo: técnico–comercial IMPAG, moderno, limpio, con fuerte presencia visual del producto, enfoque agrícola profesional.'\n\n"
        
        "OUTPUT JSON (MUY IMPORTANTE - LEE ESTO):\n"
        "- TODOS los strings JSON deben estar entre comillas dobles y CERRADOS correctamente\n"
        "- Si un string contiene saltos de línea (\\n), escápalos como \\\\n\n"
        "- Si un string contiene comillas, escápalas como \\\"\n"
        "- NUNCA dejes strings sin cerrar - cada \" debe tener su \" de cierre\n"
        "- El JSON debe ser válido y parseable\n\n"
        "EJEMPLO CORRECTO:\n"
        "{\n"
        '  "selected_category": "Categoría",\n'
        '  "selected_product_id": "123",\n'
        '  "caption": "Texto del caption con \\n\\n para saltos de línea",\n'
        '  "image_prompt": "Prompt detallado...",\n'
        '  "posting_time": "14:30",\n'
        '  "notes": "Estrategia explicada"\n'
        "}\n\n"
        "RESPONDE SOLO CON EL JSON (sin texto adicional):\n"
        "{\n"
        '  "selected_category": "...",\n'
        '  "selected_product_id": "...",\n'
        '  "caption": "...",\n'
        '  "image_prompt": "...",\n'
        '  "posting_time": "...",\n'
        '  "notes": "..."\n'
        "}"
    )

    try:
        final_resp = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            temperature=0.7,
            system="""Eres un Social Media Manager profesional. CRÍTICO: Debes responder ÚNICAMENTE con un objeto JSON válido y bien formateado.

REGLAS ESTRICTAS DE JSON:
1. Todos los strings deben estar entre comillas dobles y CERRADOS correctamente
2. Si un string contiene un salto de línea, debes usar \\n (dos caracteres: backslash seguido de n)
3. Si un string contiene comillas, debes escaparlas como \\"
4. Si un string contiene backslash, debes escaparlo como \\\\
5. NUNCA dejes un string sin cerrar - cada " de apertura debe tener su " de cierre
6. El JSON debe ser válido y parseable por json.loads()

EJEMPLO de string con saltos de línea:
"caption": "Línea 1\\n\\nLínea 2"

NO hagas esto (incorrecto):
"caption": "Línea 1

Línea 2"

Responde SOLO con el JSON, sin explicaciones ni texto adicional.""",
            messages=[{"role": "user", "content": creation_prompt}]
        )
        content = final_resp.content[0].text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Creation Error: {str(e)}")

    # 5. Final Parse with multiple fallback strategies
    data = {}
    try:
        cleaned_json = clean_json_text(content)
        data = json.loads(cleaned_json)
    except json.JSONDecodeError as e:
        print(f"Creation JSON Parse Error: {e}")
        print(f"Response text (first 500 chars): {content[:500]}")
        
        # Fallback 1: Try to repair and parse again
        try:
            repaired = repair_json_string(cleaned_json)
            data = json.loads(repaired)
            print("Successfully repaired JSON")
        except:
            # Fallback 2: Try to extract fields using regex (last resort)
            import re
            try:
                # Extract selected_category
                cat_match = re.search(r'"selected_category":\s*"([^"]*)"', content)
                if cat_match:
                    data["selected_category"] = cat_match.group(1)
                
                # Extract selected_product_id
                prod_match = re.search(r'"selected_product_id":\s*"([^"]*)"', content)
                if prod_match:
                    data["selected_product_id"] = prod_match.group(1)
                
                # Extract caption (this is the tricky one - might be unterminated)
                caption_match = re.search(r'"caption":\s*"([^"]*(?:\\.[^"]*)*)"', content, re.DOTALL)
                if not caption_match:
                    # Try to find caption even if unterminated - look for "caption": " and take until next " or }
                    caption_match = re.search(r'"caption":\s*"([^"]*(?:\\.[^"]*)*)', content, re.DOTALL)
                    if caption_match:
                        # Extract until we find a closing " or }
                        caption_text = caption_match.group(1)
                        # Remove any trailing incomplete parts
                        caption_text = caption_text.rstrip().rstrip('"').rstrip(',')
                        data["caption"] = caption_text
                else:
                    data["caption"] = caption_match.group(1).replace('\\n', '\n')
                
                # Extract image_prompt
                prompt_match = re.search(r'"image_prompt":\s*"([^"]*(?:\\.[^"]*)*)"', content, re.DOTALL)
                if not prompt_match:
                    prompt_match = re.search(r'"image_prompt":\s*"([^"]*(?:\\.[^"]*)*)', content, re.DOTALL)
                    if prompt_match:
                        prompt_text = prompt_match.group(1).rstrip().rstrip('"').rstrip(',')
                        data["image_prompt"] = prompt_text
                else:
                    data["image_prompt"] = prompt_match.group(1).replace('\\n', '\n')
                
                # Extract posting_time
                time_match = re.search(r'"posting_time":\s*"([^"]*)"', content)
                if time_match:
                    data["posting_time"] = time_match.group(1)
                
                # Extract notes
                notes_match = re.search(r'"notes":\s*"([^"]*(?:\\.[^"]*)*)"', content, re.DOTALL)
                if not notes_match:
                    notes_match = re.search(r'"notes":\s*"([^"]*(?:\\.[^"]*)*)', content, re.DOTALL)
                    if notes_match:
                        notes_text = notes_match.group(1).rstrip().rstrip('"').rstrip(',')
                        data["notes"] = notes_text
                else:
                    data["notes"] = notes_match.group(1).replace('\\n', '\n')
                
                # If we extracted at least caption, consider it a partial success
                if "caption" in data:
                    print("Partially extracted JSON fields using regex fallback")
                else:
                    raise Exception("Could not extract any fields")
            except Exception as regex_error:
                print(f"Regex extraction also failed: {regex_error}")
                # Final fallback: return minimal data
                data = {
                    "caption": content[:500] if len(content) > 500 else content,
                    "notes": f"JSON Parse Error: {str(e)}. Could not extract structured data.",
                    "image_prompt": ""
                }
    except Exception as e:
        print(f"Creation Parse Error: {e}")
        data = {
            "caption": content[:500] if len(content) > 500 else content,
            "notes": f"Parse Error: {str(e)}",
            "image_prompt": ""
        }

    # --- 6. FETCH SELECTED PRODUCT DETAILS (Back to Frontend) ---
    product_details = None
    if data.get("selected_product_id"):
        try:
             pid = int(data["selected_product_id"])
             p_obj = db.query(Product).filter(Product.id == pid).first()
             if p_obj:
                 product_details = {
                     "id": str(p_obj.id),
                     "name": p_obj.name,
                     "category": p_obj.category.name if p_obj.category else "General",
                     "sku": p_obj.sku,
                     "inStock": p_obj.stock > 0 if p_obj.stock is not None else False,
                     "price": float(p_obj.price or 0)
                 }
        except:
            pass # Use string ID or invalid ID, ignore

    return SocialGenResponse(
        caption=data.get("caption", ""),
        image_prompt=data.get("image_prompt", ""),
        posting_time=data.get("posting_time"),
        notes=data.get("notes"),
        format=data.get("format"),
        cta=data.get("cta"),
        selected_product_id=str(data.get("selected_product_id", "")),
        selected_category=data.get("selected_category"),
        selected_product_details=product_details
    )

