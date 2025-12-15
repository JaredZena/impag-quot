from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import anthropic
import os
import json
import random
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import get_db, Product, ProductCategory, SocialPost, SupplierProduct
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
    12: {"phase": "proteccion-frio", "name": "Diciembre", "actions": ["Calefacción", "Sellado invernadero", "Mantenimiento", "Planificación ciclo 2026", "Preparación suelo", "Análisis resultados"]}
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

CHANNEL_FORMATS = """
FORMATOS POR CANAL (CRÍTICO - ADAPTA EL CONTENIDO):

📱 WA STATUS (wa-status):
  - Aspecto: Vertical 9:16 (1080×1920)
  - Música: ✅ OBLIGATORIO (corridos mexicanos, regional)
  - ⚠️ CAPTION: MÍNIMO O VACÍO (máximo 50 caracteres). El contenido visual/imagen debe comunicar TODO.
  - ⚠️ PRIORIDAD: La imagen/video es lo más importante, NO el texto.
  - Duración: 15-30 segundos si es video
  - Efímero: Desaparece en 24h
  - Ejemplo: Alerta urgente, "Llegó X producto", UGC rápido

📨 WA BROADCAST (wa-broadcast):
  - Aspecto: Cuadrado 1:1 (1080×1080)
  - Música: ❌ No aplica
  - Caption: Corto pero informativo (~200 chars)
  - Ejemplo: Oferta VIP, aviso de stock

📲 WA MENSAJE (wa-message):
  - Texto conversacional, personal
  - Se puede incluir imagen cuadrada

📸 FB + IG POST (fb-post, ig-post):
  - Aspecto: Cuadrado 1:1 (1080×1080)
  - Carrusel: ✅ Hasta 10 slides
  - Música: ❌ No
  - Caption: LARGO permitido (hasta 2000 chars)
  - Se replica automáticamente FB → IG
  - Ejemplo: Infografía, carrusel educativo, caso de éxito

🎬 FB + IG REEL (fb-reel, ig-reel):
  - Aspecto: Vertical 9:16 (1080×1920)
  - Video: ✅ 15-90 segundos
  - Música: ✅ OBLIGATORIO (trending o mexicana)
  - ⚠️ CAPTION: MUY CORTO (máximo 100 caracteres). El texto principal va EN EL VIDEO con subtítulos.
  - ⚠️ PRIORIDAD: El video y su contenido visual es lo más importante, NO el caption.
  - Se replica automáticamente FB → IG
  - Hook en primeros 3 segundos
  - Ejemplo: Instalación rápida, antes/después, tip del día

📱 FB + IG STORIES (fb-story, ig-story):
  - Aspecto: Vertical 9:16 (1080×1920)
  - ⚠️ CAPTION: MÍNIMO O VACÍO (máximo 50 caracteres). El contenido visual/imagen debe comunicar TODO.
  - ⚠️ PRIORIDAD: La imagen/video es lo más importante, NO el texto.
  - Efímero: Desaparece en 24h
  - Ejemplo: Alerta urgente, promoción flash, behind-the-scenes

🎵 TIKTOK (tiktok) - ⚠️ FORMATO ESPECIAL:
  - Aspecto: Vertical 9:16 (1080×1920)
  - ⚠️ CARRUSEL DE 2-3 IMÁGENES (NO video)
  - El usuario DESLIZA para ver siguiente imagen
  - Música: ✅ OBLIGATORIO (corridos mexicanos, regional popular)
  - ⚠️ CAPTION: MUY CORTO (máximo 150 caracteres). SOLO hashtags o texto mínimo.
  - ⚠️ PRIORIDAD: TODO EL TEXTO PRINCIPAL VA EN LAS IMÁGENES DEL CARRUSEL, NO en caption.
  - ⚠️ CRÍTICO: El caption es secundario, las imágenes con texto grande son lo importante.
  - Estructura típica 3 slides:
    1. HOOK/Problema (primera imagen engancha con texto grande visible)
    2. CONTENIDO/Solución (texto en imagen)
    3. CTA/Contacto (texto en imagen)
  - Ejemplo: "3 errores al instalar" / "Antes→Después→Precio"
"""

# --- Models ---

class SocialGenRequest(BaseModel):
    date: str # YYYY-MM-DD
    # Optional overrides allow testing specific scenarios, but defaults are autonomous
    category: Optional[str] = None
    recentPostHistory: Optional[List[str]] = None
    dedupContext: Optional[Dict[str, Any]] = None
    used_in_batch: Optional[Dict[str, Any]] = None
    batch_generated_history: Optional[List[str]] = None # New field for real-time batch awareness
    suggested_topic: Optional[str] = None # User-suggested topic for the post

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
    post_type: Optional[str] = None # Post type from strategy phase (e.g., "Infografías", "Memes/tips rápidos", "Kits")
    # Channel-specific fields
    channel: Optional[str] = None # wa-status, wa-broadcast, fb-post, fb-reel, tiktok, etc.
    carousel_slides: Optional[List[str]] = None # For TikTok carousels: list of 2-3 image prompts
    needs_music: Optional[bool] = None # Whether this content needs background music
    aspect_ratio: Optional[str] = None # 1:1, 9:16, 4:5

# --- Logic ---

# Simple in-memory cache for context (key: month, value: context string)
_durango_context_cache: dict[int, str] = {}

def load_durango_context(month: int) -> str:
    """
    Load Durango sector context (agricultura, forestal, ganadería, agroindustria) from markdown files.
    Returns formatted context string for AI prompts.
    
    Uses a simple cache to avoid re-reading files on every request.
    Cache is cleared on server restart (stateless).
    """
    # Check cache first
    if month in _durango_context_cache:
        return _durango_context_cache[month]
    
    try:
        # Get the docs directory (docs is at impag-app/docs, we're in impag-quot/routes/social.py)
        # So we need to go up: impag-quot -> impag-app -> docs
        current_file = Path(__file__)  # impag-quot/routes/social.py
        project_root = current_file.parent.parent.parent  # impag-app
        docs_dir = project_root / "docs"
        
        context_parts = []
        
        # Load agricultura context
        agricultura_file = docs_dir / "durango-agricultura.md"
        if agricultura_file.exists():
            with open(agricultura_file, 'r', encoding='utf-8') as f:
                agricultura_content = f.read()
                # Extract relevant section for the month + key stats
                month_section = extract_month_section(agricultura_content, month)
                key_stats = extract_key_stats(agricultura_content, "agricultura")
                agricultura_context = month_section
                if key_stats:
                    agricultura_context = f"{key_stats}\n\n{month_section}" if month_section else key_stats
                if agricultura_context.strip():
                    context_parts.append(f"AGRICULTURA DURANGO:\n{agricultura_context}")
        
        # Load forestal context
        forestal_file = docs_dir / "durango-forestal.md"
        if forestal_file.exists():
            with open(forestal_file, 'r', encoding='utf-8') as f:
                forestal_content = f.read()
                month_section = extract_month_section(forestal_content, month)
                key_stats = extract_key_stats(forestal_content, "forestal")
                forestal_context = month_section
                if key_stats:
                    forestal_context = f"{key_stats}\n\n{month_section}" if month_section else key_stats
                if forestal_context.strip():
                    context_parts.append(f"FORESTAL DURANGO:\n{forestal_context}")
        
        # Load ganadería context
        ganaderia_file = docs_dir / "durango-ganaderia.md"
        if ganaderia_file.exists():
            with open(ganaderia_file, 'r', encoding='utf-8') as f:
                ganaderia_content = f.read()
                month_section = extract_month_section(ganaderia_content, month)
                key_stats = extract_key_stats(ganaderia_content, "ganaderia")
                ganaderia_context = month_section
                if key_stats:
                    ganaderia_context = f"{key_stats}\n\n{month_section}" if month_section else key_stats
                if ganaderia_context.strip():
                    context_parts.append(f"GANADERÍA DURANGO:\n{ganaderia_context}")
        
        # Load agroindustria context
        agroindustria_file = docs_dir / "durango-agroindustria.md"
        if agroindustria_file.exists():
            with open(agroindustria_file, 'r', encoding='utf-8') as f:
                agroindustria_content = f.read()
                # For agroindustria, include month-specific processing cycles if available
                month_section = extract_month_section(agroindustria_content, month)
                if month_section:
                    context_parts.append(f"AGROINDUSTRIA DURANGO:\n{month_section}")
                else:
                    # If no month-specific section, include key sections (Resumen, Contexto, Oportunidades)
                    summary = extract_agroindustria_summary(agroindustria_content)
                    if summary:
                        context_parts.append(f"AGROINDUSTRIA DURANGO:\n{summary}")
        
        if context_parts:
            result = "\n\n".join(context_parts)
            # Cache the result (limit cache size to avoid memory issues)
            if len(_durango_context_cache) < 12:  # Only cache up to 12 months
                _durango_context_cache[month] = result
            return result
        else:
            # Fallback to hardcoded if files don't exist
            return get_fallback_durango_context(month)
    except Exception as e:
        print(f"Error loading Durango context: {e}")
        return get_fallback_durango_context(month)

def extract_key_stats(content: str, sector: str) -> str:
    """
    Extract key statistics and rankings from markdown content.
    Looks for sections like "## Posicionamiento Nacional" or "## Estadísticas"
    """
    lines = content.split('\n')
    stats_lines = []
    in_stats_section = False
    
    # Look for key sections that contain important stats
    key_sections = [
        "posicionamiento nacional",
        "estadísticas",
        "ranking",
        "producción total",
        "valor de producción"
    ]
    
    for i, line in enumerate(lines):
        line_lower = line.lower()
        # Check if this is a relevant stats section
        if any(key in line_lower for key in key_sections) and ('##' in line or '###' in line):
            in_stats_section = True
            stats_lines.append(line)
        elif in_stats_section:
            # Stop at next major section (##) that's not a sub-section
            if line.startswith('##') and not line.startswith('###'):
                break
            # Include subsections and content
            if line.strip() and (line.startswith('#') or line.startswith('-') or ':' in line):
                stats_lines.append(line)
            # Limit to avoid too much context
            if len(stats_lines) > 15:
                break
    
    return '\n'.join(stats_lines[:15]) if stats_lines else ""

def extract_agroindustria_summary(content: str) -> str:
    """
    Extract summary sections from agroindustria markdown (Resumen, Contexto, Oportunidades).
    """
    lines = content.split('\n')
    summary_lines = []
    sections_to_include = [
        "## Resumen General",
        "## Contexto del Sector",
        "## Oportunidades"
    ]
    in_target_section = False
    current_section = None
    
    for line in lines:
        # Check if entering a target section
        if any(section in line for section in sections_to_include):
            in_target_section = True
            current_section = line
            summary_lines.append(line)
        elif in_target_section:
            # Stop at next major section (##) that's not a sub-section
            if line.startswith('##') and not line.startswith('###'):
                # Check if this is another target section
                if any(section in line for section in sections_to_include):
                    current_section = line
                    summary_lines.append(line)
                else:
                    break
            else:
                # Include content (limit length)
                if line.strip():
                    summary_lines.append(line)
                    if len(summary_lines) > 60:  # Limit total lines
                        break
    
    return '\n'.join(summary_lines) if summary_lines else ""

def extract_month_section(content: str, month: int) -> str:
    """
    Extract the section relevant to the given month from markdown content.
    Looks for sections like "### Enero-Febrero" or "## Ciclos por Mes"
    """
    month_names = {
        1: ["enero", "febrero"], 2: ["enero", "febrero"],
        3: ["marzo", "abril"], 4: ["marzo", "abril"],
        5: ["mayo", "junio", "julio"], 6: ["mayo", "junio", "julio"], 7: ["mayo", "junio", "julio"],
        8: ["agosto", "septiembre"], 9: ["agosto", "septiembre"],
        10: ["octubre", "noviembre"], 11: ["octubre", "noviembre"],
        12: ["diciembre"]
    }
    
    target_months = month_names.get(month, [])
    if not target_months:
        return ""
    
    lines = content.split('\n')
    in_relevant_section = False
    section_lines = []
    current_section = []
    
    for line in lines:
        # Check if this is a month header
        line_lower = line.lower()
        if any(month_name in line_lower for month_name in target_months) and ('###' in line or '##' in line):
            in_relevant_section = True
            current_section = [line]
        elif in_relevant_section:
            # Stop at next major section (## or ###)
            if line.startswith('##') and not any(month_name in line.lower() for month_name in target_months):
                break
            current_section.append(line)
    
    if current_section:
        return '\n'.join(current_section)
    
    # If no specific month section found, return summary from "Ciclos por Mes" section
    return extract_general_cycles(content)

def extract_general_cycles(content: str) -> str:
    """Extract general cycle information if month-specific not found."""
    lines = content.split('\n')
    in_cycles_section = False
    cycles_lines = []
    
    for line in lines:
        if 'ciclos' in line.lower() and ('##' in line or '###' in line):
            in_cycles_section = True
        elif in_cycles_section:
            if line.startswith('##') and 'ciclos' not in line.lower():
                break
            if line.strip():
                cycles_lines.append(line)
    
    return '\n'.join(cycles_lines[:20]) if cycles_lines else "Información general del sector disponible."

def get_fallback_durango_context(month: int) -> str:
    """Fallback context if markdown files are not available."""
    if month in [1, 2]:
        return "Ciclos Durango: Preparación siembra maíz/frijol (feb-mar), mantenimiento avena/alfalfa/trigo (cultivos de frío otoño-invierno). Forestal: Protección árboles jóvenes contra heladas, mantenimiento viveros forestales. Ganadero: Alimentación suplementaria, protección ganado contra frío, mantenimiento cercas y corrales."
    elif month in [3, 4]:
        return "Ciclos Durango: Siembra maíz/frijol activa, crecimiento avena/alfalfa/trigo, inicio manzana. Forestal: Siembra/reforestación activa, trasplante árboles, preparación viveros. Ganadero: Pastoreo primaveral, reparación cercas post-invierno, preparación agostaderos."
    elif month in [5, 6, 7]:
        return "Ciclos Durango: Crecimiento maíz/frijol, cosecha avena/alfalfa, desarrollo manzana, inicio chile. Forestal: Crecimiento activo árboles, mantenimiento reforestaciones, control plagas forestales. Ganadero: Pastoreo intensivo, construcción/reparación cercas, protección sombra para ganado, preparación henificación."
    elif month in [8, 9]:
        return "Ciclos Durango: Cosecha manzana (ago-sep), desarrollo chile, preparación siembra otoño-invierno (avena, trigo, cultivos de frío). Forestal: Mantenimiento reforestaciones, preparación viveros otoño-invierno, protección contra incendios. Ganadero: Cosecha forraje, henificación, preparación alimentación invernal, mantenimiento infraestructura ganadera."
    elif month in [10, 11]:
        return "Ciclos Durango: Cosecha frijol (oct-nov), cosecha chile (oct-nov), siembra activa avena/trigo (cultivos de frío otoño-invierno), preparación protección frío. Forestal: Siembra otoño-invierno especies forestales, protección árboles contra heladas tempranas, mantenimiento viveros. Ganadero: Almacenamiento forraje, preparación protección ganado frío, reparación cercas y corrales, alimentación suplementaria inicio."
    elif month == 12:
        return "Ciclos Durango: Protección heladas crítica, mantenimiento invernal cultivos de frío (avena/trigo), preparación nuevo ciclo. Forestal: Protección árboles contra heladas, mantenimiento viveros invernal, planificación reforestación siguiente año. Ganadero: Protección ganado heladas crítica, alimentación suplementaria intensiva, mantenimiento cercas y refugios, preparación próximo ciclo."
    return ""

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
    Fetch random active supplier products from the database with full details for ranking.
    Uses SupplierProduct table which has embeddings for semantic search.
    """
    db_products = db.query(SupplierProduct).join(ProductCategory, SupplierProduct.category_id == ProductCategory.id).filter(
        SupplierProduct.is_active == True,
        SupplierProduct.archived_at == None
    ).limit(limit).all()
    
    catalog = []
    for sp in db_products:
        cat_name = sp.category.name if sp.category else (sp.product.category.name if sp.product and sp.product.category else "General")
        catalog.append({
            "id": str(sp.id),
            "name": sp.name or (sp.product.name if sp.product else "Unknown"),
            "category": cat_name,
            "inStock": sp.stock > 0 if sp.stock is not None else False,
            "sku": sp.sku or (sp.product.sku if sp.product else ""),
            "description": sp.description or (sp.product.description if sp.product else "") or "",
            "specifications": sp.specifications or (sp.product.specifications if sp.product else {}) or {},
            "hasDescription": bool((sp.description or (sp.product.description if sp.product else "")) and len((sp.description or (sp.product.description if sp.product else "")).strip()) > 20),
            "hasSpecs": bool((sp.specifications or (sp.product.specifications if sp.product else {})) and (isinstance(sp.specifications or (sp.product.specifications if sp.product else {}), dict) and len((sp.specifications or (sp.product.specifications if sp.product else {}))) > 0))
        })
    return catalog

def calculate_product_interest_score(product: Dict[str, Any], topic: str = "") -> float:
    """
    Calculate an interest score for a product based on how engaging/educational it can be.
    Higher score = more interesting for customers.
    """
    score = 0.0
    
    # Products with descriptions are more engaging (can tell a story)
    if product.get("hasDescription"):
        desc_len = len(product.get("description", ""))
        if desc_len > 100:
            score += 3.0  # Detailed descriptions are very engaging
        elif desc_len > 50:
            score += 2.0
        else:
            score += 1.0
    
    # Products with specifications are educational (teachable moments)
    if product.get("hasSpecs"):
        score += 2.5  # Technical specs = educational content potential
    
    # Products with both description and specs are highly engaging
    if product.get("hasDescription") and product.get("hasSpecs"):
        score += 1.5  # Bonus for having both
    
    # Category relevance (some categories are more visually interesting)
    category = product.get("category", "").lower()
    visually_interesting_categories = [
        "bombeo-solar", "kits", "estructuras", "riego", 
        "mallasombra", "antiheladas", "acolchado"
    ]
    if any(cat in category for cat in visually_interesting_categories):
        score += 1.0  # These categories make for better visual content
    
    # Topic relevance (if topic matches product category/keywords)
    if topic:
        topic_lower = topic.lower()
        product_name_lower = product.get("name", "").lower()
        category_lower = category
        
        # Check if topic keywords match product
        topic_words = set(topic_lower.split())
        product_words = set(product_name_lower.split() + category_lower.split())
        if topic_words.intersection(product_words):
            score += 2.0  # High relevance to topic
    
    # Products with SKU suggest they're catalog items (more professional)
    if product.get("sku"):
        score += 0.5
    
    return score

def search_products(db: Session, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search supplier products using semantic search with embeddings when available,
    falling back to text search (ILIKE) if embeddings are not available.
    Uses SupplierProduct table which has embeddings for semantic search.
    """
    if not query:
        return fetch_db_products(db, limit) # Fallback to random if no query

    # Try semantic search with embeddings first
    try:
        from rag_system_moved.embeddings import generate_embeddings
        query_embedding = generate_embeddings([query])[0]
        
        # Use vector similarity search for products with embeddings
        db_products = db.query(SupplierProduct).join(
            ProductCategory, SupplierProduct.category_id == ProductCategory.id
        ).filter(
            SupplierProduct.is_active == True,
            SupplierProduct.archived_at == None,
            SupplierProduct.embedding != None
        ).order_by(
            SupplierProduct.embedding.cosine_distance(query_embedding)
        ).limit(limit).all()
        
        if db_products:
            # Convert to catalog format
            catalog = []
            for sp in db_products:
                cat_name = sp.category.name if sp.category else (sp.product.category.name if sp.product and sp.product.category else "General")
                catalog.append({
                    "id": str(sp.id),
                    "name": sp.name or (sp.product.name if sp.product else "Unknown"),
                    "category": cat_name,
                    "inStock": sp.stock > 0 if sp.stock is not None else False,
                    "sku": sp.sku or (sp.product.sku if sp.product else ""),
                    "description": sp.description or (sp.product.description if sp.product else "") or "",
                    "specifications": sp.specifications or (sp.product.specifications if sp.product else {}) or {},
                    "hasDescription": bool((sp.description or (sp.product.description if sp.product else "")) and len((sp.description or (sp.product.description if sp.product else "")).strip()) > 20),
                    "hasSpecs": bool((sp.specifications or (sp.product.specifications if sp.product else {})) and (isinstance(sp.specifications or (sp.product.specifications if sp.product else {}), dict) and len((sp.specifications or (sp.product.specifications if sp.product else {}))) > 0))
                })
            return catalog
    except Exception as e:
        print(f"Embedding search failed, falling back to text search: {e}")
    
    # Fallback to text search (ILIKE) if embeddings fail or no results
    terms = query.split()
    
    # Search supplier products by name (active only)
    db_products = db.query(SupplierProduct).join(
        ProductCategory, SupplierProduct.category_id == ProductCategory.id
    ).filter(
        SupplierProduct.is_active == True,
        SupplierProduct.archived_at == None,
        SupplierProduct.name.ilike(f"%{query}%")
    ).limit(limit).all()
    
    # If loose match needed:
    if not db_products and len(terms) > 0:
         # Fallback: search by first word
         db_products = db.query(SupplierProduct).join(
             ProductCategory, SupplierProduct.category_id == ProductCategory.id
         ).filter(
             SupplierProduct.is_active == True,
             SupplierProduct.archived_at == None,
             SupplierProduct.name.ilike(f"%{terms[0]}%")
         ).limit(limit).all()

    catalog = []
    for sp in db_products:
        cat_name = sp.category.name if sp.category else (sp.product.category.name if sp.product and sp.product.category else "General")
        catalog.append({
            "id": str(sp.id),
            "name": sp.name or (sp.product.name if sp.product else "Unknown"),
            "category": cat_name,
            "inStock": sp.stock > 0 if sp.stock is not None else False,
            "sku": sp.sku or (sp.product.sku if sp.product else ""),
            "description": sp.description or (sp.product.description if sp.product else "") or "",
            "specifications": sp.specifications or (sp.product.specifications if sp.product else {}) or {},
            "hasDescription": bool((sp.description or (sp.product.description if sp.product else "")) and len((sp.description or (sp.product.description if sp.product else "")).strip()) > 20),
            "hasSpecs": bool((sp.specifications or (sp.product.specifications if sp.product else {})) and (isinstance(sp.specifications or (sp.product.specifications if sp.product else {}), dict) and len((sp.specifications or (sp.product.specifications if sp.product else {}))) > 0))
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
    # Channel-specific fields
    channel: Optional[str] = None  # wa-status, fb-post, tiktok, etc.
    carousel_slides: Optional[List[str]] = None  # Array of slide prompts for carousels (TikTok, FB/IG)
    needs_music: Optional[bool] = False
    user_feedback: Optional[str] = None  # 'like', 'dislike', or None

@router.get("/posts")
async def get_social_posts(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """
    Get all social posts (shared across all users).
    Can filter by date range and status.
    """
    try:
        query = db.query(SocialPost)
        
        # Filter by date range if provided
        if start_date:
            query = query.filter(SocialPost.date_for >= start_date)
        if end_date:
            query = query.filter(SocialPost.date_for <= end_date)
        
        # Filter by status if provided
        if status:
            query = query.filter(SocialPost.status == status)
        
        # Order by date_for (target date) and creation time
        posts = query.order_by(SocialPost.date_for.desc(), SocialPost.created_at.desc()).all()
        
        return {
            "status": "success",
            "count": len(posts),
            "posts": [
                {
                    "id": p.id,
                    "date_for": p.date_for,
                    "caption": p.caption,
                    "image_prompt": p.image_prompt,
                    "post_type": p.post_type,
                    "status": p.status,
                    "selected_product_id": p.selected_product_id,
                    "formatted_content": p.formatted_content,
                    "channel": p.channel,
                    "carousel_slides": p.carousel_slides,
                    "needs_music": p.needs_music,
                    "user_feedback": p.user_feedback,
                    "created_at": p.created_at.isoformat() if p.created_at else None
                }
                for p in posts
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/posts/by-date/{date}")
async def get_social_posts_by_date(
    date: str,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """
    Get all posts for a specific date (YYYY-MM-DD).
    """
    try:
        posts = db.query(SocialPost).filter(
            SocialPost.date_for == date
        ).order_by(SocialPost.created_at.desc()).all()
        
        return {
            "status": "success",
            "date": date,
            "count": len(posts),
            "posts": [
                {
                    "id": p.id,
                    "date_for": p.date_for,
                    "caption": p.caption,
                    "image_prompt": p.image_prompt,
                    "post_type": p.post_type,
                    "status": p.status,
                    "selected_product_id": p.selected_product_id,
                    "formatted_content": p.formatted_content,
                    "channel": p.channel,
                    "carousel_slides": p.carousel_slides,
                    "needs_music": p.needs_music,
                    "user_feedback": p.user_feedback,
                    "created_at": p.created_at.isoformat() if p.created_at else None
                }
                for p in posts
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save")
async def save_social_post(
    payload: SocialPostSaveRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """Save or update a generated/approved post to the backend history (shared across all users).
    If a post with the same formatted_content.id exists, it will be updated instead of creating a new one.
    """
    try:
        # Validate user_feedback if provided
        if payload.user_feedback and payload.user_feedback not in ['like', 'dislike']:
            raise HTTPException(status_code=400, detail="user_feedback must be 'like', 'dislike', or None")
        
        # Check if post already exists (by formatted_content.id or DB ID)
        existing_post = None
        if payload.formatted_content and payload.formatted_content.get('id'):
            target_id = payload.formatted_content.get('id')
            
            # First, try to extract DB ID if format is "db-{id}"
            db_id_match = None
            if isinstance(target_id, str) and target_id.startswith('db-'):
                try:
                    db_id_match = int(target_id.replace('db-', ''))
                    # Try to find by DB ID first (most reliable)
                    existing_post = db.query(SocialPost).filter(SocialPost.id == db_id_match).first()
                except ValueError:
                    pass
            
            # If not found by DB ID, search by formatted_content.id in JSON field
            if not existing_post:
                posts = db.query(SocialPost).filter(
                    SocialPost.formatted_content.isnot(None)
                ).all()
                for p in posts:
                    if p.formatted_content and isinstance(p.formatted_content, dict):
                        if p.formatted_content.get('id') == target_id:
                            existing_post = p
                            break
        
        if existing_post:
            # Update existing post
            existing_post.date_for = payload.date_for
            existing_post.caption = payload.caption
            existing_post.image_prompt = payload.image_prompt
            existing_post.post_type = payload.post_type
            existing_post.status = payload.status
            existing_post.selected_product_id = payload.selected_product_id
            existing_post.formatted_content = payload.formatted_content
            existing_post.channel = payload.channel
            existing_post.carousel_slides = payload.carousel_slides
            existing_post.needs_music = payload.needs_music
            existing_post.user_feedback = payload.user_feedback
            db.commit()
            db.refresh(existing_post)
            return {"status": "success", "id": existing_post.id, "updated": True}
        else:
            # Create new post
            new_post = SocialPost(
                date_for=payload.date_for,
                caption=payload.caption,
                image_prompt=payload.image_prompt,
                post_type=payload.post_type,
                status=payload.status,
                selected_product_id=payload.selected_product_id,
                formatted_content=payload.formatted_content,
                channel=payload.channel,
                carousel_slides=payload.carousel_slides,
                needs_music=payload.needs_music,
                user_feedback=payload.user_feedback
            )
            db.add(new_post)
            db.commit()
            db.refresh(new_post)
            return {"status": "success", "id": new_post.id, "updated": False}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

class FeedbackUpdateRequest(BaseModel):
    feedback: Optional[str] = None  # 'like', 'dislike', or None

@router.put("/posts/{post_id}/feedback")
async def update_post_feedback(
    post_id: int,
    payload: FeedbackUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token) # Optional auth
):
    """Update user feedback for an existing post."""
    try:
        # Validate feedback if provided
        if payload.feedback and payload.feedback not in ['like', 'dislike']:
            raise HTTPException(status_code=400, detail="feedback must be 'like', 'dislike', or None")
        
        post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        
        post.user_feedback = payload.feedback
        db.commit()
        db.refresh(post)
        
        return {
            "status": "success",
            "id": post.id,
            "user_feedback": post.user_feedback
        }
    except HTTPException:
        raise
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

    # --- 0. CONTEXT INIT (needed for history query) ---
    try:
        dt = datetime.strptime(payload.date, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    
    # --- 0. FETCH HISTORY (BACKEND) ---
    # Fetch last 20 posts from last 10 days to avoid repetition
    from datetime import timedelta
    ten_days_ago = dt - timedelta(days=10)
    recent_posts = db.query(SocialPost).filter(
        SocialPost.date_for >= ten_days_ago.strftime("%Y-%m-%d"),
        SocialPost.date_for <= payload.date
    ).order_by(SocialPost.created_at.desc()).limit(20).all()
    
    # Build comprehensive history with post_type, channel, topic, and product
    history_items = []
    for p in recent_posts:
        # Extract topic from caption (first line or first 40 chars)
        topic_hint = p.caption.split('\n')[0][:40] if p.caption else "Sin tema"
        if len(topic_hint) < 10:
            topic_hint = p.caption[:40] if p.caption else "Sin tema"
        
        # Build history entry with all relevant info
        entry_parts = []
        if p.post_type:
            entry_parts.append(f"Tipo: {p.post_type}")
        if p.channel:
            entry_parts.append(f"Canal: {p.channel}")
        entry_parts.append(f"Tema: {topic_hint}...")
        if p.selected_product_id:
            entry_parts.append(f"Producto ID: {p.selected_product_id}")
        
        history_items.append(" | ".join(entry_parts))
    
    # Add batch history if present (posts generated just now in this session)
    if payload.batch_generated_history:
        history_items.extend(payload.batch_generated_history)

    recent_history = "\n- ".join(history_items) if history_items else "Sin historial previo."
    
    # Extract deduplication info from recent posts
    recent_product_ids = set()
    recent_categories = set()
    for p in recent_posts:
        if p.selected_product_id:
            recent_product_ids.add(str(p.selected_product_id))
        if p.formatted_content and isinstance(p.formatted_content, dict):
            products = p.formatted_content.get('products', [])
            for prod in products:
                if isinstance(prod, dict):
                    if prod.get('id'):
                        recent_product_ids.add(str(prod['id']))
                    if prod.get('category'):
                        recent_categories.add(prod['category'])
    
    # Also use dedup context from frontend if provided (more comprehensive)
    if payload.dedupContext:
        recent_product_ids.update(str(pid) for pid in payload.dedupContext.get('recent_product_ids', []))
        recent_categories.update(payload.dedupContext.get('recent_categories', []))
    
    # Check for products used in current batch (to avoid duplicates in same generation)
    used_in_batch_ids = set()
    used_in_batch_categories = set()
    if payload.used_in_batch:
        used_in_batch_ids.update(str(pid) for pid in payload.used_in_batch.get('product_ids', []))
        used_in_batch_categories.update(payload.used_in_batch.get('categories', []))

    # --- 1. SEASON CONTEXT ---
    sales_context = get_season_context(dt)
    important_dates = str([d["name"] for d in get_nearby_dates(dt)])
    
    # Calculate variety metrics for post_type, channel, and topics
    recent_types = [p.post_type for p in recent_posts if p.post_type]
    recent_channels = [p.channel for p in recent_posts if p.channel]
    recent_topics = []  # Extract topics from captions
    recent_topic_keywords = set()  # Extract keywords from topics for better deduplication
    for p in recent_posts:
        if p.caption:
            # Try to extract topic (first line or first meaningful phrase)
            topic = p.caption.split('\n')[0].strip()
            if len(topic) > 10 and len(topic) < 100:
                recent_topics.append(topic.lower())
                # Extract keywords (remove emojis, common words)
                import re
                topic_clean = re.sub(r'[^\w\s]', ' ', topic.lower())
                keywords = [w for w in topic_clean.split() if len(w) > 4 and w not in ['para', 'con', 'del', 'las', 'los', 'una', 'uno', 'este', 'esta', 'estos', 'estas']]
                recent_topic_keywords.update(keywords)
    
    # Count promos and analyze post type variety
    db_promo_count = sum(1 for t in recent_types if t and ('promo' in t.lower() or 'venta' in t.lower() or 'promoción' in t.lower() or 'promoción puntual' in t.lower()))
    batch_promo_count = 0
    if payload.batch_generated_history:
        batch_promo_count = sum(1 for item in payload.batch_generated_history if 'promo' in item.lower() or 'venta' in item.lower() or 'promoción' in item.lower())

    total_recent = len(recent_types) + (len(payload.batch_generated_history) if payload.batch_generated_history else 0)
    promo_count = db_promo_count + batch_promo_count
    
    # More strict: penalize if > 20% are promos OR if last 2 posts were promos
    last_two_are_promo = len(recent_types) >= 2 and all(
        t and ('promo' in t.lower() or 'venta' in t.lower() or 'promoción' in t.lower()) 
        for t in recent_types[-2:]
    )
    penalize_promo = (total_recent > 0 and (promo_count / total_recent) > 0.2) or last_two_are_promo
    
    # Analyze post type distribution
    from collections import Counter
    type_counter = Counter([t.lower() for t in recent_types if t])
    most_common_type = type_counter.most_common(1)[0][0] if type_counter else None
    most_common_count = type_counter.most_common(1)[0][1] if type_counter else 0
    
    # If same type used 2+ times in recent posts, warn
    type_repetition_warning = ""
    if most_common_count >= 2 and total_recent >= 2:
        type_repetition_warning = f"⛔ ALERTA: El tipo '{most_common_type}' se ha usado {most_common_count} veces recientemente. ELIGE UN TIPO DIFERENTE hoy.\n"
    
    # Also check if last 2-3 posts are the same type
    if len(recent_types) >= 2:
        last_two_types = [t.lower() if t else '' for t in recent_types[-2:]]
        if last_two_types[0] == last_two_types[1] and last_two_types[0] != '':
            type_repetition_warning += f"⛔ ALERTA: Los últimos 2 posts fueron del tipo '{last_two_types[0]}'. ESTÁ PROHIBIDO usar este tipo hoy.\n"
    
    # Analyze channel variety
    channel_counts = {}
    for ch in recent_channels:
        channel_counts[ch] = channel_counts.get(ch, 0) + 1
    
    # Analyze topic variety (check for repeated topics)
    topic_counts = {}
    for topic in recent_topics:
        # Normalize topic (remove common words)
        normalized = ' '.join([w for w in topic.split() if len(w) > 4])
        if normalized:
            topic_counts[normalized] = topic_counts.get(normalized, 0) + 1

    # --- 2. STRATEGY PHASE ---
    # Check if we're over-focusing on a single topic (e.g., calefacción)
    # Use both topics and keywords for better detection
    calefaccion_count = sum(1 for t in recent_topics if 'calefacc' in t or 'calefacción' in t)
    calefaccion_count += sum(1 for k in recent_topic_keywords if 'calefacc' in k)
    heladas_count = sum(1 for t in recent_topics if 'helada' in t)
    heladas_count += sum(1 for k in recent_topic_keywords if 'helada' in k)
    invernadero_count = sum(1 for t in recent_topics if 'invernader' in t)
    invernadero_count += sum(1 for k in recent_topic_keywords if 'invernader' in k)
    mantenimiento_count = sum(1 for t in recent_topics if 'mantenimiento' in t)
    mantenimiento_count += sum(1 for k in recent_topic_keywords if 'mantenimiento' in k)
    
    # Also check in captions for more comprehensive detection
    for p in recent_posts:
        if p.caption:
            caption_lower = p.caption.lower()
            if 'calefacc' in caption_lower or 'calefacción' in caption_lower:
                calefaccion_count += 0.5  # Partial match
            if 'helada' in caption_lower:
                heladas_count += 0.5
            if 'invernader' in caption_lower:
                invernadero_count += 0.5
    
    over_focus_warning = ""
    if calefaccion_count >= 2:
        over_focus_warning = f"⛔ ALERTA CRÍTICA: Ya se han generado {int(calefaccion_count)} posts sobre calefacción recientemente. ESTÁ PROHIBIDO usar este tema hoy. Busca otros temas relevantes para la temporada.\n"
    if heladas_count >= 3:
        over_focus_warning += f"⛔ ALERTA CRÍTICA: Ya se han generado {int(heladas_count)} posts sobre heladas recientemente. ESTÁ PROHIBIDO usar este tema hoy. Elige un tema completamente diferente.\n"
    if mantenimiento_count >= 3:
        over_focus_warning += f"⛔ ALERTA: Ya se han generado {int(mantenimiento_count)} posts sobre mantenimiento recientemente. Varía el tema significativamente.\n"
    if invernadero_count >= 5:
        over_focus_warning += f"⛔ ALERTA: Ya se han generado {int(invernadero_count)} posts sobre invernaderos recientemente. Considera otros temas agrícolas (campo abierto, ganadería, forestal, etc.).\n"
    
    # Suggest alternative topics for December
    alternative_topics_december = [
        "Planificación del ciclo primavera 2026",
        "Optimización de recursos y costos",
        "Preparación de suelo para próximo ciclo",
        "Análisis de resultados del año",
        "Nuevas tecnologías y tendencias",
        "Gestión de inventario y almacén",
        "Capacitación y educación agrícola",
        "Sustentabilidad y buenas prácticas",
        "Optimización de riego",
        "Manejo de cultivos de frío (avena, trigo, alfalfa)"
    ]
    
    # Build strategy prompt (avoiding backslashes in f-string expressions)
    strategy_prompt = f"ACTÚA COMO: Director de Estrategia Comercial. FECHA: {payload.date}\n"
    strategy_prompt += f"FASE AGRÍCOLA: {sales_context['phase']} ({sales_context['name']}).\n"
    strategy_prompt += f"ACCIONES SUGERIDAS: {', '.join(sales_context['actions'])}.\n"
    strategy_prompt += f"EFEMÉRIDES: {important_dates}.\n"
    strategy_prompt += f"PREFERENCIA USUARIO: {payload.category or 'Ninguna (Decide tú)'}.\n"
    
    # Add suggested topic if provided
    if payload.suggested_topic:
        strategy_prompt += f"💡 TEMA SUGERIDO POR EL USUARIO: {payload.suggested_topic}\n⚠️ USA ESTE TEMA COMO BASE, pero puedes adaptarlo o expandirlo según sea necesario.\n\n"
    
    # Add over focus warning if present
    if over_focus_warning:
        strategy_prompt += f"{over_focus_warning}\n"
    
    # Continue building the prompt
    strategy_prompt += "HISTORIAL RECIENTE (TUS ÚLTIMAS DECISIONES):\n"
    strategy_prompt += f"- {recent_history or 'Sin historial previo.'}\n\n"
    
    # Add alternative topics if needed
    if over_focus_warning and dt.month == 12:
        alt_topics = ', '.join(alternative_topics_december[:5]) + '...'
        strategy_prompt += f"💡 TEMAS ALTERNATIVOS SUGERIDOS (para evitar repetición): {alt_topics}\n"
    
    if dt.month == 12 and (calefaccion_count >= 1 or heladas_count >= 2):
        alt_topics_full = ', '.join(alternative_topics_december)
        strategy_prompt += f"📋 TEMAS ALTERNATIVOS PARA DICIEMBRE (si necesitas ideas): {alt_topics_full}\n"
    
    strategy_prompt += "REGLAS DE VARIEDAD (CRÍTICO - SÍGUELAS O FALLARÁ LA ESTRATEGIA):\n"
    
    # Add promo penalty warning if needed
    if penalize_promo:
        strategy_prompt += "⛔ ALERTA: EXCESO DE PROMOS DETECTADO. ESTÁ PROHIBIDO ELEGIR TIPO `Promoción puntual` PARA ESTE DÍA. USA EDUCATIVO/ENGAGEMENT.\n"
    
    # Add type repetition warning if needed
    if type_repetition_warning:
        strategy_prompt += type_repetition_warning
    
    strategy_prompt += (
        "- NO repitas el mismo TIPO de post que el día anterior.\n"
        "- NO repitas el mismo TIPO de post que en los últimos 2 días.\n"
        "- NO repitas el mismo CANAL que el día anterior (varía entre wa-status, fb-post, tiktok, etc.).\n"
        "- NO repitas el mismo TEMA/TOPIC que en los últimos 3 días.\n"
        "- NO repitas el mismo PRODUCTO que en los últimos 3 días.\n"
        "- El éxito depende de mezclar: Venta (20%), Educación (40%), Entretenimiento (20%), Comunidad (20%).\n"
        "- PRIORIZA tipos EDUCATIVOS como: Infografías, Memes/tips rápidos, Tutoriales, Checklist operativo, FAQ/Mitos.\n"
        "- PRIORIZA tipos de ENGAGEMENT como: Caso de éxito/UGC, Antes/Después, Convocatoria a UGC.\n"
        "- USA 'Promoción puntual' SOLO cuando realmente haya una oferta especial o liquidación.\n"
        "- VARÍA los canales: no uses siempre fb-post, alterna con wa-status, tiktok, reels.\n"
        "- VARÍA los temas: aunque la fase agrícola sea 'protección contra frío', puedes hablar de:\n"
        "  * Preparación para el siguiente ciclo\n"
        " * Planificación y organización\n"
        " * Mantenimiento preventivo general (no solo calefacción)\n"
        " * Educación sobre otros temas agrícolas\n"
        " * Casos de éxito y resultados\n"
        " * NO te limites solo a calefacción/heladas - hay más temas relevantes.\n\n"
        
        f"ANÁLISIS DE VARIEDAD RECIENTE:\n"
        f"- Tipos de post usados (últimos {len(recent_types)}): {', '.join(recent_types[-5:]) if recent_types else 'Ninguno'}\n"
        f"- Distribución de tipos: {dict(type_counter.most_common(5)) if type_counter else 'Ninguno'}\n"
        f"- ⚠️ Si ves que un tipo se repite 2+ veces, ELIGE UNO DIFERENTE.\n"
        f"- Canales usados: {', '.join(set(recent_channels[:5])) if recent_channels else 'Ninguno'}\n"
        f"- Temas/topics recientes: {', '.join(recent_topics[:5]) if recent_topics else 'Ninguno'}\n"
        f"- Palabras clave frecuentes: {', '.join(list(recent_topic_keywords)[:8]) if recent_topic_keywords else 'Ninguna'}\n"
        f"- ⚠️ EVITA repetir estos temas/palabras clave en tu decisión de hoy.\n"
        f"- ⚠️ Si ves muchas menciones de 'calefacción', 'heladas', 'invernadero', elige un tema DIFERENTE.\n"
        f"- ⚠️ CONTEO ESPECÍFICO: Calefacción={calefaccion_count}, Heladas={heladas_count}, Invernaderos={invernadero_count}, Mantenimiento={mantenimiento_count}\n"
        f"- ⚠️ CONTEO DE PROMOS: {promo_count} de {total_recent} posts recientes son promos ({promo_count/total_recent*100:.0f}%)\n"
        f"- Si algún conteo es >= 2, EVITA ese tema completamente hoy.\n\n"
    )
    
    strategy_prompt += "TIPOS DE POST DISPONIBLES (ELIGE UNO DE ESTA LISTA - VARÍA RESPECTO A LOS ÚLTIMOS DÍAS):\n"
    strategy_prompt += f"{POST_TYPES_DEFINITIONS}\n\n"
    
    strategy_prompt += "TIPOS RECOMENDADOS PARA VARIEDAD (prioriza estos si has usado muchos 'Promoción puntual'):\n"
    strategy_prompt += "- Infografías: Muy educativo, alto engagement\n"
    strategy_prompt += "- Memes/tips rápidos: Divertido, fácil de compartir\n"
    strategy_prompt += "- Tutorial corto: Educativo y práctico\n"
    strategy_prompt += "- Checklist operativo: Útil y accionable\n"
    strategy_prompt += "- Caso de éxito / UGC: Prueba social, genera confianza\n"
    strategy_prompt += "- Antes / Después: Visualmente impactante\n"
    strategy_prompt += "- FAQ / Mitos: Remueve objeciones\n"
    strategy_prompt += "- ROI / números rápidos: Justifica inversión\n"
    strategy_prompt += "\n"
    strategy_prompt += "⚠️ USA 'Promoción puntual' SOLO cuando realmente haya una oferta especial, liquidación o alta rotación urgente.\n"
    strategy_prompt += "⚠️ NO uses 'Promoción puntual' como tipo por defecto - varía con tipos educativos y de engagement.\n\n"

    strategy_prompt += "TU TAREA: Decide el TEMA del post de hoy y el TIPO DE POST exacto.\n"
    strategy_prompt += "IMPORTANTE SOBRE TEMAS (CRÍTICO):\n"
    strategy_prompt += "- Las 'ACCIONES SUGERIDAS' son solo sugerencias, NO son obligatorias.\n"
    strategy_prompt += "- Puedes elegir temas relacionados pero DIFERENTES a las acciones sugeridas.\n"
    strategy_prompt += "- Ejemplo: Si la acción es 'Calefacción', puedes hablar de:\n"
    strategy_prompt += "  * Planificación del siguiente ciclo (maíz, frijol para primavera)\n"
    strategy_prompt += "  * Preparación de suelo para siembra\n"
    strategy_prompt += "  * Optimización de recursos y costos\n"
    strategy_prompt += "  * Educación sobre otros aspectos agrícolas (riego, fertilización, etc.)\n"
    strategy_prompt += "  * Casos de éxito o resultados del año\n"
    strategy_prompt += "  * Gestión de inventario y organización\n"
    strategy_prompt += "  * Cultivos de frío actuales (avena, trigo, alfalfa) - no solo invernaderos\n"
    strategy_prompt += "  * Tecnología y innovación agrícola\n"
    strategy_prompt += "- VARÍA los temas incluso dentro de la misma fase agrícola.\n"
    strategy_prompt += "- NO te limites solo a 'protección contra frío' - hay muchos otros temas relevantes en diciembre.\n"
    strategy_prompt += "- Considera que en diciembre también se prepara para el ciclo primavera-verano.\n\n"
    strategy_prompt += "RESPONDE SOLO CON EL JSON:\n"
    strategy_prompt += "{\n"
    strategy_prompt += '  "topic": "Tema principal (ej. Preparación de suelo, Planificación ciclo 2026, Optimización recursos) - DEBE SER DIFERENTE a temas recientes",\n'
    strategy_prompt += '  "post_type": "Escribe EXACTAMENTE el nombre del tipo (ej. Infografías, Memes/tips rápidos, Kits, etc.)",\n'
    strategy_prompt += '  "channel": "wa-status|wa-broadcast|fb-post|fb-reel|ig-post|ig-reel|tiktok (elige uno, DIFERENTE al usado ayer)",\n'
    strategy_prompt += '  "preferred_category": "Categoría de producto preferida (ej. riego, mallasombra, fertilizantes) o vacío si no hay preferencia",\n'
    strategy_prompt += '  "search_needed": true/false,\n'
    strategy_prompt += '  "search_keywords": "términos de búsqueda para embeddings (ej. arado, fertilizante inicio, protección heladas)"\n'
    strategy_prompt += "}"
    
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
        strat_data = {"topic": "General", "post_type": "Infografías", "channel": "fb-post", "preferred_category": "", "search_needed": True, "search_keywords": ""}
    except Exception as e:
        print(f"Strategy Error: {e}")
        # Fallback Strategy
        strat_data = {"topic": "General", "post_type": "Infografías", "channel": "fb-post", "preferred_category": "", "search_needed": True, "search_keywords": ""}

    # --- 3. PRODUCT SELECTION PHASE (using embeddings) ---
    selected_product_id = None
    selected_category = None
    
    if strat_data.get("search_needed"):
        search_query = strat_data.get("search_keywords", "") or strat_data.get("topic", "")
        preferred_category = strat_data.get("preferred_category", "")
        
        # Use semantic search with embeddings
        try:
            from rag_system_moved.embeddings import generate_embeddings
            query_embedding = generate_embeddings([search_query])[0]
            
            # Build query for supplier products with embeddings
            product_query = db.query(SupplierProduct).join(
                ProductCategory, SupplierProduct.category_id == ProductCategory.id
            ).filter(
                SupplierProduct.is_active == True,
                SupplierProduct.archived_at == None,
                SupplierProduct.embedding != None
            )
            
            # Filter by preferred category if specified
            if preferred_category:
                product_query = product_query.filter(
                    ProductCategory.name.ilike(f"%{preferred_category}%")
                )
            
            # Get top products by vector similarity
            candidate_products = product_query.order_by(
                SupplierProduct.embedding.cosine_distance(query_embedding)
            ).limit(30).all()
            
            # Filter out recently used products
            filtered_candidates = []
            for sp in candidate_products:
                sp_id_str = str(sp.id)
                # Skip if used recently
                if sp_id_str in recent_product_ids or sp_id_str in used_in_batch_ids:
                    continue
                # Skip if category was heavily used
                cat_name = sp.category.name if sp.category else (sp.product.category.name if sp.product and sp.product.category else "General")
                category_count = sum(1 for cat in recent_categories if cat.lower() == cat_name.lower())
                if category_count >= 3:
                    continue
                filtered_candidates.append(sp)
            
            # If filtering removed everything, allow some repeats
            if not filtered_candidates:
                for sp in candidate_products[:10]:
                    sp_id_str = str(sp.id)
                    if sp_id_str not in used_in_batch_ids:
                        filtered_candidates.append(sp)
                        break
            
            # Select the best product (first in similarity-ordered list after filtering)
            if filtered_candidates:
                selected_sp = filtered_candidates[0]
                selected_product_id = str(selected_sp.id)
                selected_category = selected_sp.category.name if selected_sp.category else (selected_sp.product.category.name if selected_sp.product and selected_sp.product.category else "General")
            
        except Exception as e:
            print(f"Embedding-based product selection failed: {e}")
            # Fallback to text search
            keywords = strat_data.get("search_keywords", "") or strat_data.get("topic", "")
            found_products = search_products(db, keywords, limit=10)
            
            # Filter and select
            for p in found_products:
                if p['id'] not in recent_product_ids and p['id'] not in used_in_batch_ids:
                    selected_product_id = p['id']
                    selected_category = p['category']
                    break

    # --- 4. CONTENT GENERATION PHASE ---
    # Fetch selected product details if a product was selected
    selected_product_info = ""
    if selected_product_id:
        try:
            pid = int(selected_product_id)
            sp_obj = db.query(SupplierProduct).filter(SupplierProduct.id == pid).first()
            if sp_obj:
                product_name = sp_obj.name or (sp_obj.product.name if sp_obj.product else "Unknown")
                product_desc = sp_obj.description or (sp_obj.product.description if sp_obj.product else "") or "Sin descripción disponible"
                product_specs = sp_obj.specifications or (sp_obj.product.specifications if sp_obj.product else {}) or {}
                specs_str = ", ".join([f"{k}: {v}" for k, v in product_specs.items()]) if isinstance(product_specs, dict) and len(product_specs) > 0 else str(product_specs) if product_specs else "No disponibles"
                cat_name = sp_obj.category.name if sp_obj.category else (sp_obj.product.category.name if sp_obj.product and sp_obj.product.category else "General")
                sku = sp_obj.sku or (sp_obj.product.sku if sp_obj.product else "N/A")
                
                selected_product_info = (
                    f"\n📦 PRODUCTO SELECCIONADO (USA ESTA INFORMACIÓN PARA GENERAR CONTENIDO PRECISO):\n"
                    f"- ID: {selected_product_id}\n"
                    f"- Nombre: {product_name}\n"
                    f"- Categoría: {cat_name}\n"
                    f"- SKU: {sku}\n"
                    f"- Descripción: {product_desc}\n"
                    f"- Especificaciones: {specs_str}\n"
                    f"\n⚠️ IMPORTANTE: El caption y el prompt de imagen DEBEN reflejar el uso real, propósito y características de este producto específico.\n"
                    f"Investiga mentalmente: ¿Para qué se usa este producto? ¿En qué cultivos? ¿Qué problema resuelve? ¿Cómo se instala/usa?\n"
                    f"Enfócate en el valor educativo y el interés del producto para generar contenido atractivo.\n"
                )
        except Exception as e:
            print(f"Error fetching product details: {e}")
            selected_product_info = f"\nProducto seleccionado ID: {selected_product_id}\n"

    # Build deduplication context for AI (includes products, categories, channels, topics)
    dedup_info = ""
    if recent_product_ids or recent_categories or recent_channels or recent_topics:
        dedup_info = "\n\n⚠️ IMPORTANTE - EVITA REPETIR (Últimos 10 días):\n"
        if recent_product_ids:
            dedup_info += f"- Productos usados: {len(recent_product_ids)} productos diferentes\n"
        if recent_categories:
            dedup_info += f"- Categorías usadas: {', '.join(list(recent_categories)[:5])}\n"
        if recent_channels:
            unique_channels = list(set(recent_channels))
            dedup_info += f"- Canales usados recientemente: {', '.join(unique_channels[:5])}\n"
            dedup_info += "  → VARÍA el canal: no uses el mismo que ayer. Alterna entre wa-status, fb-post, tiktok, reels.\n"
        if recent_topics:
            # Show most common topics to avoid
            from collections import Counter
            topic_counter = Counter(recent_topics)
            top_topics = [t for t, _ in topic_counter.most_common(3)]
            dedup_info += f"- Topics/temas recientes: {', '.join([t[:30] for t in top_topics])}\n"
        if used_in_batch_ids:
            dedup_info += f"- Productos ya usados en esta generación: {len(used_in_batch_ids)} productos\n"
        dedup_info += "\nREGLAS:\n"
        dedup_info += "- Puedes repetir el TEMA (ej. heladas) pero usa DIFERENTES productos o soluciones.\n"
        dedup_info += "- Si el tema es urgente (heladas, siembra), está bien repetirlo por 1 semana pero variando productos.\n"
        dedup_info += "- NO uses el mismo CANAL que el post de ayer.\n"
        dedup_info += "- NO uses el mismo TIPO DE POST que el post de ayer.\n"
    
    # Load Durango sector context from markdown files
    durango_context = load_durango_context(month=dt.month)

    creation_prompt = (
        f"ACTÚA COMO: Social Media Manager especializado en contenido agrícola.\n\n"
        f"ESTRATEGIA DEFINIDA:\n"
        f"- TEMA: {strat_data.get('topic')}\n"
        f"- TIPO DE POST: {strat_data.get('post_type')}\n"
        f"- CANAL: {strat_data.get('channel')}\n"
        f"{selected_product_info}\n"
        f"CONTEXTO REGIONAL DURANGO (USA ESTA INFORMACIÓN PARA CONTENIDO RELEVANTE, PERO NO TE LIMITES SOLO A ESTO):\n"
        f"{durango_context}\n\n"
        f"⚠️ NOTA SOBRE EL CONTEXTO: El contexto de Durango menciona actividades estacionales, pero NO debes limitarte solo a esos temas.\n"
        f"Puedes hablar de otros temas relevantes como planificación, optimización, educación, casos de éxito, etc.\n\n"
        f"{dedup_info}\n"
        
        f"{CHANNEL_FORMATS}\n\n"
        
        "INSTRUCCIONES:\n"
        "1. El producto ya fue seleccionado en la fase anterior. Usa la información del producto proporcionada arriba.\n"
        "2. Si NO hay producto seleccionado, crea un post genérico de marca/educativo sobre el tema.\n"
        "3. EL CANAL ya fue definido en la estrategia: {strat_data.get('channel')}. Adapta el contenido a este canal específico.\n"
        f"   ⚠️ Canales usados recientemente: {', '.join(set(recent_channels[:5])) if recent_channels else 'Ninguno'}\n"
        f"   → El canal '{strat_data.get('channel')}' ya fue seleccionado. Asegúrate de adaptar el contenido a este canal.\n\n"
        "   ⚠️⚠️⚠️ REGLAS CRÍTICAS DE CAPTION POR CANAL ⚠️⚠️⚠️:\n"
        "   - WA STATUS (wa-status): Caption MÍNIMO (máx 50 chars). La imagen/video comunica TODO.\n"
        "   - FB/IG STORIES: Caption MÍNIMO (máx 50 chars). La imagen/video comunica TODO.\n"
        "   - TIKTOK: Caption MUY CORTO (máx 150 chars). TODO el texto va EN LAS IMÁGENES del carrusel.\n"
        "   - REELS (fb-reel, ig-reel): Caption CORTO (máx 100 chars). El texto principal va EN EL VIDEO con subtítulos.\n"
        "   - FB/IG POST (fb-post, ig-post): Caption puede ser LARGO (hasta 2000 chars). Aquí sí puedes ser detallado.\n\n"
        "   IMPORTANTE: Para wa-status, stories, tiktok y reels, el CAPTION es secundario.\n"
        "   La IMAGEN/VIDEO debe ser autoexplicativa y comunicar el mensaje completo.\n\n"
        "   - Si es TikTok: Genera prompts para 2-3 imágenes del carrusel CON TEXTO GRANDE EN CADA IMAGEN\n"
        "   - Si es Reel: Indica que necesita música y texto EN EL VIDEO con subtítulos\n"
        "   - Si es WA Status: Formato vertical, contenido urgente/directo, caption mínimo\n"
        "   - Si es FB/IG Post: Puede ser más detallado y educativo, caption largo permitido\n"
        "5. Genera el contenido adaptado al canal, respetando las reglas de caption arriba.\n\n"
        
        "--- INSTRUCCIONES ESPECÍFICAS PARA image_prompt ---\n"
        "El campo 'image_prompt' DEBE ser un prompt detallado y técnico para generación de imágenes (estilo IMPAG).\n"
        "Sigue este formato estructurado:\n\n"
        
        "⚠️⚠️⚠️ ADAPTACIÓN POR CANAL (CRÍTICO) ⚠️⚠️⚠️:\n"
        "- Para wa-status, stories, tiktok, reels: La imagen DEBE ser AUTOEXPLICATIVA con TEXTO GRANDE Y VISIBLE.\n"
        "  El usuario debe entender el mensaje SOLO viendo la imagen, sin leer el caption.\n"
        "- Para fb-post, ig-post: La imagen puede ser más técnica/detallada, el caption puede complementar.\n\n"
        
        "FORMATO REQUERIDO (adaptar dimensiones al canal):\n"
        "- wa-status/stories/tiktok/reels: Vertical 1080×1920 px\n"
        "- fb-post/ig-post: Cuadrado 1080×1080 px\n"
        "Estilo [flyer técnico/paisaje agrícola/catálogo técnico] IMPAG, con diseño limpio, moderno y profesional.\n"
        "Mantén siempre la estética corporativa IMPAG: fondo agrícola difuminado, tonos blanco–gris, acentos verde–azul, sombras suaves, tipografías gruesas para títulos y delgadas para texto técnico.\n\n"
        
        "Instrucciones de diseño detalladas:\n"
        "1. LOGOS (OBLIGATORIO):\n"
        "   - Logo IMPAG: Colocar el logo oficial de 'IMPAG Agricultura Inteligente' en la esquina superior derecha, sin deformarlo y manteniendo la proporción.\n"
        "   - Logo 'Todo para el Campo': Si el contexto lo permite, incluir también el logo de 'Todo para el Campo' en la esquina inferior izquierda o derecha (según composición).\n"
        "   - Ambos logos deben ser visibles, nítidos y con buen contraste sobre el fondo.\n"
        "   - Los logos son parte esencial de la identidad visual IMPAG - NUNCA los omitas.\n\n"
        
        "2. ELEMENTO PRINCIPAL (CON PERSONAS CUANDO APLIQUE):\n"
        "   - Si hay producto: Imagen realista del producto en alta resolución, fotorealista, iluminación de estudio suave o golden hour.\n"
        "   - ⚠️ INCLUYE PERSONAS cuando sea apropiado:\n"
        "     * Para productos agrícolas: Agricultor/productor mexicano usando el producto en campo, sosteniéndolo, o mostrándolo como recomendación.\n"
        "     * Para productos ganaderos: Ganadero usando el producto, mostrándolo en uso real.\n"
        "     * Para productos forestales: Ingeniero forestal o trabajador forestal usando el producto.\n"
        "     * Para productos de riego/instalación: Ingeniero agrónomo o técnico instalando o mostrando el producto.\n"
        "     * Las personas deben verse profesionales, auténticas, con ropa de trabajo agrícola/ganadero/forestal apropiada.\n"
        "     * Las personas deben estar interactuando con el producto de forma natural (sosteniéndolo, instalándolo, usándolo).\n"
        "   - Si es paisaje: Paisaje agrícola realista del norte de México (Durango), cultivos en hileras, iluminación natural suave.\n"
        "   - Si es kit: Componentes completamente visibles, montados o desglosados en técnica 'knolling', cables ordenados.\n"
        "   - Mantener proporción, ubicación, integración suave con fondo, estilo profesional tipo catálogo.\n"
        "   ⚠️ PARA STORIES/STATUS/TIKTOK/REELS: Agrega TEXTO GRANDE Y VISIBLE en la imagen que comunique el mensaje principal.\n"
        "   El texto debe ser legible desde lejos, con buen contraste, tamaño mínimo 60-80px.\n\n"
        
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
        "- 'Genera una imagen cuadrada 1080×1080 px, estilo flyer técnico IMPAG, con diseño limpio, moderno y profesional. Mantén siempre la estética corporativa IMPAG: fondo agrícola difuminado, tonos blanco–gris, acentos verde–azul, sombras suaves, tipografías gruesas para títulos y delgadas para texto técnico. Logo IMPAG 'Agricultura Inteligente' en esquina superior derecha, logo 'Todo para el Campo' en esquina inferior izquierda. Imagen realista del producto [nombre] en alta resolución, fotorealista, con un agricultor/productor mexicano sosteniéndolo o usándolo en campo, iluminación de estudio suave o golden hour. El agricultor debe verse profesional, auténtico, con ropa de trabajo agrícola. Bloque técnico con especificaciones: [lista de specs]. Pie del flyer: todoparaelcampo.com.mx, Envíos a todo México, WhatsApp: 677-119-7737. Estilo: técnico–comercial IMPAG, moderno, limpio, con fuerte presencia visual del producto, enfoque agrícola profesional.'\n\n"
        "- 'Imagen vertical 1080×1920 px para WA Status, estilo story IMPAG. Logo IMPAG en esquina superior derecha, logo Todo para el Campo en esquina inferior. TEXTO GRANDE Y VISIBLE en el centro comunicando el mensaje principal (tamaño mínimo 80px, buen contraste). Imagen del producto [nombre] destacada, con un ingeniero agrónomo o técnico mostrándolo como recomendación, en campo agrícola de Durango, iluminación natural. El técnico debe verse profesional, sosteniendo o señalando el producto. Colores vibrantes pero naturales. El texto en la imagen comunica TODO el mensaje sin necesidad de leer el caption.'\n\n"
        
        "OUTPUT JSON (MUY IMPORTANTE - LEE ESTO):\n"
        "- TODOS los strings JSON deben estar entre comillas dobles y CERRADOS correctamente\n"
        "- Si un string contiene saltos de línea (\\n), escápalos como \\\\n\n"
        "- Si un string contiene comillas, escápalas como \\\"\n"
        "- NUNCA dejes strings sin cerrar - cada \" debe tener su \" de cierre\n"
        "- El JSON debe ser válido y parseable\n"
        "- IMPORTANTE PARA CARRUSELES: Si el canal es 'tiktok' o 'fb-post' con carrusel, usa 'carousel_slides' en lugar de 'image_prompt'\n\n"
        "EJEMPLO CORRECTO (post normal con producto):\n"
        "{\n"
        '  "selected_category": "riego",\n'
        '  "selected_product_id": "123",\n'
        '  "channel": "fb-post",\n'
        '  "caption": "Texto del caption con \\n\\n para saltos de línea",\n'
        '  "image_prompt": "Prompt detallado para UNA imagen...",\n'
        '  "needs_music": false,\n'
        '  "posting_time": "14:30",\n'
        '  "notes": "Estrategia explicada"\n'
        "}\n\n"
        "EJEMPLO CORRECTO (post genérico sin producto específico):\n"
        "{\n"
        '  "selected_category": "",\n'
        '  "selected_product_id": "",\n'
        '  "channel": "fb-post",\n'
        '  "caption": "Contenido educativo general...",\n'
        '  "image_prompt": "Prompt para imagen genérica...",\n'
        '  "needs_music": false,\n'
        '  "posting_time": "10:00",\n'
        '  "notes": "Post educativo sin producto específico"\n'
        "}\n\n"
        "EJEMPLO CORRECTO (carrusel TikTok o FB/IG):\n"
        "{\n"
        '  "selected_category": "mallasombra",\n'
        '  "selected_product_id": "456",\n'
        '  "channel": "tiktok",\n'
        '  "caption": "Texto corto...",\n'
        '  "carousel_slides": [\n'
        '    "SLIDE 1 (HOOK): Imagen vertical 9:16, texto grande: ¿Error #1 en invernaderos?...",\n'
        '    "SLIDE 2 (CONTENIDO): Imagen vertical 9:16, muestra la solución...",\n'
        '    "SLIDE 3 (CTA): Imagen vertical 9:16, contacto y llamada a acción..."\n'
        '  ],\n'
        '  "needs_music": true,\n'
        '  "posting_time": "10:00",\n'
        '  "notes": "Carrusel TikTok con 3 slides..."\n'
        "}\n\n"
        "EJEMPLO CORRECTO (WA Status - caption mínimo):\n"
        "{\n"
        '  "selected_category": "Categoría",\n'
        '  "selected_product_id": "123",\n'
        '  "channel": "wa-status",\n'
        '  "caption": "🔥 Llegó hoy",\n'
        '  "image_prompt": "Imagen vertical 1080×1920, estilo story IMPAG. Logo IMPAG \'Agricultura Inteligente\' en esquina superior derecha, logo \'Todo para el Campo\' en esquina inferior izquierda. TEXTO GRANDE Y VISIBLE en el centro (tamaño 80px, buen contraste): \\"NUEVO PRODUCTO LLEGÓ HOY\\". Imagen del producto [nombre específico] destacada, con un agricultor/productor mexicano sosteniéndolo o mostrándolo como recomendación, en campo agrícola de Durango, iluminación natural golden hour. El agricultor debe verse profesional, auténtico, con ropa de trabajo agrícola. Colores vibrantes pero naturales. El texto en la imagen comunica TODO el mensaje sin necesidad de leer el caption.",\n'
        '  "needs_music": true,\n'
        '  "posting_time": "10:00",\n'
        '  "notes": "WA Status - imagen autoexplicativa, caption mínimo"\n'
        "}\n\n"
        "RESPONDE SOLO CON EL JSON (sin texto adicional):\n"
        "{\n"
        '  "selected_category": "...",\n'
        '  "selected_product_id": "...",\n'
        f'  "channel": "{strat_data.get("channel", "fb-post")}",\n'
        '  "caption": "... (RESPETA: wa-status/stories/tiktok/reels = MUY CORTO, fb-post = puede ser largo)",\n'
        '  "image_prompt": "... (SOLO si es post de 1 imagen. Para stories/status debe ser autoexplicativa)",\n'
        '  "carousel_slides": ["Slide 1 CON TEXTO GRANDE...", "Slide 2 CON TEXTO...", ...] (SOLO si es carrusel: TikTok 2-3, FB/IG 2-10),\n'
        '  "needs_music": true/false,\n'
        '  "posting_time": "...",\n'
        '  "notes": "..."\n'
        "}\n\n"
        "⚠️ RECUERDA: Para wa-status, stories, tiktok y reels, el caption debe ser MÍNIMO.\n"
        "La imagen/video debe comunicar el mensaje completo sin depender del caption.\n\n"
        "⚠️⚠️⚠️ REGLAS FINALES CRÍTICAS ⚠️⚠️⚠️:\n"
        f"1. ⚠️ PRODUCTO: El producto ya fue seleccionado (ID: {selected_product_id or 'ninguno'}). Usa esta información para generar contenido específico.\n"
        "2. SIEMPRE incluye el logo IMPAG 'Agricultura Inteligente' en esquina superior derecha.\n"
        "3. Cuando sea apropiado, incluye el logo 'Todo para el Campo' en esquina inferior.\n"
        "4. SIEMPRE incluye personas (agricultores, productores, ingenieros, técnicos) usando o mostrando el producto cuando sea relevante.\n"
        "5. El caption y el prompt de imagen DEBEN reflejar el uso real, propósito y características específicas del producto seleccionado.\n"
        "6. NO uses descripciones genéricas - sé específico sobre el producto y su uso en agricultura/ganadería/forestal de Durango."
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
             sp_obj = db.query(SupplierProduct).filter(SupplierProduct.id == pid).first()
             if sp_obj:
                 cat_name = sp_obj.category.name if sp_obj.category else (sp_obj.product.category.name if sp_obj.product and sp_obj.product.category else "General")
                 # Calculate price from supplier cost + shipping + margin
                 cost = float(sp_obj.cost or 0)
                 shipping = float(sp_obj.shipping_cost_direct or 0)
                 margin = float(sp_obj.default_margin or 0.30)  # Default 30% margin
                 price = (cost + shipping) / (1 - margin) if margin < 1 else cost + shipping
                 
                 product_details = {
                     "id": str(sp_obj.id),
                     "name": sp_obj.name or (sp_obj.product.name if sp_obj.product else "Unknown"),
                     "category": cat_name,
                     "sku": sp_obj.sku or (sp_obj.product.sku if sp_obj.product else ""),
                     "inStock": sp_obj.stock > 0 if sp_obj.stock is not None else False,
                     "price": price
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
        selected_product_id=selected_product_id or str(data.get("selected_product_id", "")),  # Use from product selection phase
        selected_category=selected_category or data.get("selected_category"),  # Use from product selection phase
        selected_product_details=product_details,
        post_type=strat_data.get("post_type"),  # From strategy phase
        channel=strat_data.get("channel") or data.get("channel"),  # From strategy phase, fallback to content phase
        carousel_slides=data.get("carousel_slides"),
        needs_music=data.get("needs_music")
    )


