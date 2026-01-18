from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import anthropic
import os
import json
import random
from datetime import datetime, date as date_type
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from collections import Counter
from models import get_db, Product, ProductCategory, SocialPost, SupplierProduct
from auth import verify_google_token

# Import new modules (same directory)
import sys
from pathlib import Path
routes_dir = Path(__file__).parent
if str(routes_dir) not in sys.path:
    sys.path.insert(0, str(routes_dir))
import social_context
import social_dedupe
import social_products
import social_llm
import social_rate_limit
import social_logging
import social_topic

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
    selected_categories: Optional[List[str]] = None # Categories selected by frontend (will be moved to backend)

class SocialGenBatchRequest(BaseModel):
    date: str # YYYY-MM-DD
    min_count: int = 1
    max_count: int = 3
    suggested_topic: Optional[str] = None
    category_override: Optional[str] = None # Optional category preference

class SocialGenResponse(BaseModel):
    caption: str
    image_prompt: Optional[str] = None  # Optional - carousel posts use carousel_slides instead
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
    # Topic-based deduplication fields (CRITICAL)
    topic: Optional[str] = None # Topic in format "Problema → Solución" (canonical unit of deduplication)
    problem_identified: Optional[str] = None # Problem description from strategy phase
    saved_post_id: Optional[int] = None # ID of the automatically saved post in database

class SocialGenBatchResponse(BaseModel):
    posts: List[SocialGenResponse]
    selected_categories: List[str] # Categories selected by backend
    metadata: Dict[str, Any] # Month phase, important dates, etc.

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
        social_logging.safe_log_error(f"Error loading Durango context: {e}", exc_info=True)
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

# validate_problem_focused_topic removed - use social_topic.validate_topic instead

def identify_agricultural_problems(
    month: int,
    phase: str,
    nearby_dates: list,
    durango_context: str
) -> dict:
    """
    Identifies real agricultural problems based on season, phase, and regional context.
    Returns problems with urgency, impact, and solution categories.
    """
    # Problem database by month/phase
    problem_map = {
        # Enero (Germinación + Planificación)
        1: {
            "urgent": [
                {
                    "problem": "Heladas matutinas queman plántulas recién emergidas",
                    "symptoms": "Plántulas con hojas quemadas, muerte de semillas germinadas",
                    "impact": "Pérdida 30-50% de germinación, retraso en ciclo",
                    "solution_category": "antiheladas",
                    "urgency": "high",
                    "time_window": "Enero-Febrero",
                    "common_mistake": "No proteger charolas en invernadero sin calefacción"
                },
                {
                    "problem": "Falta de planificación causa pérdidas económicas en ciclo primavera-verano",
                    "symptoms": "Compras apresuradas, pérdida de ventanas de siembra, menor rendimiento",
                    "impact": "Reducción 20-30% en rentabilidad, desabasto de insumos",
                    "solution_category": "general",
                    "urgency": "high",
                    "time_window": "Enero-Febrero",
                    "common_mistake": "Iniciar ciclo sin plan de siembra ni presupuesto"
                },
                {
                    "problem": "Descontrol de gastos impide conocer rentabilidad real",
                    "symptoms": "No se sabe cuánto se gastó, qué fue rentable, qué no",
                    "impact": "Decisiones sin datos, repetición de errores costosos",
                    "solution_category": "general",
                    "urgency": "high",
                    "time_window": "Todo el año",
                    "common_mistake": "No registrar gastos ni resultados por ciclo"
                }
            ],
            "preventive": [
                {
                    "problem": "Sustrato seco en charolas causa germinación desigual",
                    "symptoms": "Algunas cavidades germinan, otras no",
                    "impact": "Plántulas desuniformes, pérdida de tiempo",
                    "solution_category": "riego",
                    "urgency": "medium",
                    "time_window": "Enero-Marzo",
                    "common_mistake": "Regar después de llenar en lugar de antes"
                },
                {
                    "problem": "Suelo no preparado reduce rendimiento 20-40% en ciclo primavera-verano",
                    "symptoms": "Raíces débiles, plantas atrofiadas, menor producción",
                    "impact": "Reducción significativa en rendimiento y rentabilidad",
                    "solution_category": "acolchado",
                    "urgency": "medium",
                    "time_window": "Enero-Febrero",
                    "common_mistake": "Sembrar sin preparar suelo adecuadamente"
                },
                {
                    "problem": "Charolas sucias transmiten enfermedades a nuevas siembras",
                    "symptoms": "Damping-off, pudrición de raíces",
                    "impact": "Pérdida 20-40% de plántulas",
                    "solution_category": "charolas",
                    "urgency": "medium",
                    "time_window": "Todo el año",
                    "common_mistake": "Reutilizar charolas sin desinfectar"
                }
            ]
        },
        # Febrero (Trasplante)
        2: {
            "urgent": [
                {
                    "problem": "Plántulas se estresan en trasplante y no prenden",
                    "symptoms": "Plántulas marchitas, hojas amarillas, muerte post-trasplante",
                    "impact": "Pérdida 25-40% de plántulas trasplantadas",
                    "solution_category": "vivero",
                    "urgency": "high",
                    "time_window": "Febrero-Marzo",
                    "common_mistake": "Trasplantar sin endurecer plántulas"
                },
                {
                    "problem": "Suelo no preparado causa raíces débiles",
                    "symptoms": "Raíces no penetran, plantas atrofiadas",
                    "impact": "Crecimiento lento, menor producción",
                    "solution_category": "acolchado",
                    "urgency": "high",
                    "time_window": "Febrero",
                    "common_mistake": "Trasplantar en suelo compactado sin preparar"
                }
            ],
            "preventive": [
                {
                    "problem": "Riego por surco desperdicia 70% del agua en febrero",
                    "symptoms": "Agua corriendo fuera del surco, suelo seco entre plantas",
                    "impact": "Costo alto de agua, plantas estresadas",
                    "solution_category": "riego",
                    "urgency": "medium",
                    "time_window": "Febrero-Abril",
                    "common_mistake": "Usar riego tradicional en lugar de goteo"
                }
            ]
        },
        # Marzo-Abril (Crecimiento)
        3: {
            "urgent": [
                {
                    "problem": "Calor intenso quema hojas y reduce fotosíntesis",
                    "symptoms": "Hojas quemadas, plantas estresadas, menor crecimiento",
                    "impact": "Reducción 20-30% en producción",
                    "solution_category": "mallasombra",
                    "urgency": "high",
                    "time_window": "Marzo-Mayo",
                    "common_mistake": "No instalar sombra antes del calor"
                },
                {
                    "problem": "Riego irregular causa estrés hídrico y menor rendimiento",
                    "symptoms": "Hojas marchitas intermitentes, frutos pequeños",
                    "impact": "Reducción 15-25% en producción",
                    "solution_category": "riego",
                    "urgency": "high",
                    "time_window": "Marzo-Julio",
                    "common_mistake": "Regar por horario en lugar de por necesidad"
                }
            ]
        },
        4: {
            "urgent": [
                {
                    "problem": "Calor intenso quema hojas y reduce fotosíntesis",
                    "symptoms": "Hojas quemadas, plantas estresadas, menor crecimiento",
                    "impact": "Reducción 20-30% en producción",
                    "solution_category": "mallasombra",
                    "urgency": "high",
                    "time_window": "Marzo-Mayo",
                    "common_mistake": "No instalar sombra antes del calor"
                },
                {
                    "problem": "Riego irregular causa estrés hídrico y menor rendimiento",
                    "symptoms": "Hojas marchitas intermitentes, frutos pequeños",
                    "impact": "Reducción 15-25% en producción",
                    "solution_category": "riego",
                    "urgency": "high",
                    "time_window": "Marzo-Julio",
                    "common_mistake": "Regar por horario en lugar de por necesidad"
                }
            ]
        },
        # Mayo-Junio (Cosecha)
        5: {
            "urgent": [
                {
                    "problem": "Cosecha tardía reduce calidad y precio de venta",
                    "symptoms": "Frutos sobre-maduros, menor precio en mercado",
                    "impact": "Pérdida 20-30% en valor de venta",
                    "solution_category": "herramientas",
                    "urgency": "medium",
                    "time_window": "Mayo-Junio",
                    "common_mistake": "Esperar a que todos los frutos maduren"
                }
            ]
        },
        6: {
            "urgent": [
                {
                    "problem": "Cosecha tardía reduce calidad y precio de venta",
                    "symptoms": "Frutos sobre-maduros, menor precio en mercado",
                    "impact": "Pérdida 20-30% en valor de venta",
                    "solution_category": "herramientas",
                    "urgency": "medium",
                    "time_window": "Mayo-Junio",
                    "common_mistake": "Esperar a que todos los frutos maduren"
                }
            ]
        },
        # Julio (Lluvias)
        7: {
            "urgent": [
                {
                    "problem": "Exceso de humedad causa pudrición y enfermedades fúngicas",
                    "symptoms": "Pudrición de frutos, hojas con manchas, plantas enfermas",
                    "impact": "Pérdida 30-50% de producción en lluvias intensas",
                    "solution_category": "plasticos",
                    "urgency": "high",
                    "time_window": "Julio-Agosto",
                    "common_mistake": "No tener drenaje adecuado en invernaderos"
                },
                {
                    "problem": "Lluvias lavan nutrientes del suelo",
                    "symptoms": "Plantas amarillas, crecimiento lento post-lluvia",
                    "impact": "Necesidad de re-fertilizar, costo adicional",
                    "solution_category": "fertilizantes",
                    "urgency": "medium",
                    "time_window": "Julio",
                    "common_mistake": "No proteger fertilizantes aplicados antes de lluvia"
                }
            ]
        },
        8: {
            "preventive": [
                {
                    "problem": "Preparación inadecuada para ciclo otoño-invierno",
                    "symptoms": "Suelo no preparado, falta de planificación",
                    "impact": "Retraso en siembra, menor productividad",
                    "solution_category": "general",
                    "urgency": "medium",
                    "time_window": "Agosto-Septiembre",
                    "common_mistake": "No planificar con anticipación"
                }
            ]
        },
        9: {
            "urgent": [
                {
                    "problem": "Siembra tardía reduce ventana de crecimiento",
                    "symptoms": "Cultivos no alcanzan madurez antes de heladas",
                    "impact": "Pérdida de producción, necesidad de protección temprana",
                    "solution_category": "general",
                    "urgency": "high",
                    "time_window": "Septiembre-Octubre",
                    "common_mistake": "Retrasar siembra por falta de preparación"
                }
            ]
        },
        10: {
            "preventive": [
                {
                    "problem": "Falta de protección temprana contra frío",
                    "symptoms": "Cultivos vulnerables a primeras heladas",
                    "impact": "Pérdida parcial o total si helada temprana",
                    "solution_category": "antiheladas",
                    "urgency": "medium",
                    "time_window": "Octubre-Noviembre",
                    "common_mistake": "Esperar a que haya helada para proteger"
                }
            ]
        },
        # Noviembre-Diciembre (Frío/Heladas)
        11: {
            "urgent": [
                {
                    "problem": "Heladas matan cultivos de ciclo otoño-invierno",
                    "symptoms": "Plantas congeladas, hojas negras, muerte total",
                    "impact": "Pérdida 100% del cultivo si no se protege",
                    "solution_category": "antiheladas",
                    "urgency": "critical",
                    "time_window": "Noviembre-Enero",
                    "common_mistake": "No instalar protección hasta que ya hay helada"
                },
                {
                    "problem": "Cultivos de invierno (avena, trigo) no resisten heladas extremas",
                    "symptoms": "Plantas congeladas, pérdida de forraje",
                    "impact": "Pérdida total si temperatura baja de -5°C",
                    "solution_category": "antiheladas",
                    "urgency": "critical",
                    "time_window": "Diciembre-Enero",
                    "common_mistake": "Confiar solo en resistencia natural de cultivos"
                }
            ]
        },
        12: {
            "urgent": [
                {
                    "problem": "Heladas matan cultivos de ciclo otoño-invierno",
                    "symptoms": "Plantas congeladas, hojas negras, muerte total",
                    "impact": "Pérdida 100% del cultivo si no se protege",
                    "solution_category": "antiheladas",
                    "urgency": "critical",
                    "time_window": "Noviembre-Enero",
                    "common_mistake": "No instalar protección hasta que ya hay helada"
                },
                {
                    "problem": "Cultivos de invierno (avena, trigo) no resisten heladas extremas",
                    "symptoms": "Plantas congeladas, pérdida de forraje",
                    "impact": "Pérdida total si temperatura baja de -5°C",
                    "solution_category": "antiheladas",
                    "urgency": "critical",
                    "time_window": "Diciembre-Enero",
                    "common_mistake": "Confiar solo en resistencia natural de cultivos"
                }
            ]
        }
    }
    
    # Get problems for current month
    month_problems = problem_map.get(month, {})
    
    # Add phase-specific problems
    phase_problems = {
        "germinacion": [
            {
                "problem": "Temperatura inadecuada retrasa o impide germinación",
                "symptoms": "Semillas no germinan, tiempo de germinación muy largo",
                "impact": "Retraso en ciclo, pérdida de ventana de siembra",
                "solution_category": "vivero",
                "urgency": "high"
            }
        ],
        "proteccion-frio": [
            {
                "problem": "Heladas sorpresa sin protección causan pérdidas totales",
                "symptoms": "Cultivos completamente congelados en una noche",
                "impact": "Pérdida 100% del cultivo, inversión perdida",
                "solution_category": "antiheladas",
                "urgency": "critical"
            }
        ]
    }
    
    # Combine and prioritize
    all_problems = []
    if month_problems.get("urgent"):
        all_problems.extend(month_problems["urgent"])
    if month_problems.get("preventive"):
        all_problems.extend(month_problems["preventive"])
    if phase in phase_problems:
        all_problems.extend(phase_problems[phase])
    
    # Check nearby dates for additional urgency
    for date in nearby_dates:
        if isinstance(date, dict) and date.get("type") == "seasonal" and "helada" in str(date.get("name", "")).lower():
            # Boost helada-related problems
            for prob in all_problems:
                if "helada" in prob.get("problem", "").lower():
                    prob["urgency"] = "critical"
                    if "daysUntil" in date:
                        prob["days_until"] = date.get("daysUntil", 0)
    
    return {
        "problems": all_problems,
        "most_urgent": [p for p in all_problems if p.get("urgency") == "critical"],
        "high_priority": [p for p in all_problems if p.get("urgency") == "high"],
        "month": month,
        "phase": phase
    }

def get_nearby_dates(date_obj):
    # Simple logic: return dates in the same month
    month = date_obj.month
    return [d for d in DEFAULT_DATES if d["month"] == month]

def fetch_db_products_old(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
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
        return social_products.fetch_db_products(db, limit) # Fallback to random if no query

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
        social_logging.safe_log_warning(f"Embedding search failed, falling back to text search: {e}")
    
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
    # Topic-based deduplication fields (CRITICAL)
    topic: str  # Topic in format "Problema → Solución" (REQUIRED - comes from LLM or must be provided)
    problem_identified: Optional[str] = None  # Problem description from strategy phase

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
        
        # Filter by date range if provided (FIXED: Use DATE comparison, not string)
        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(SocialPost.date_for >= start_date_obj)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid start_date format: {start_date}. Expected YYYY-MM-DD")
        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                query = query.filter(SocialPost.date_for <= end_date_obj)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid end_date format: {end_date}. Expected YYYY-MM-DD")
        
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
                    "topic": p.topic,
                    "problem_identified": p.problem_identified,
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
        # FIXED: Use DATE comparison, not string
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {date}. Expected YYYY-MM-DD")
        posts = db.query(SocialPost).filter(
            SocialPost.date_for == date_obj
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
                    "topic": p.topic,
                    "problem_identified": p.problem_identified,
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
    """Update an existing post (status, user_feedback, etc.).
    
    NOTE: New posts are automatically saved by /generate endpoint.
    This endpoint is primarily for updating existing posts.
    """
    try:
        # Validate user_feedback if provided
        if payload.user_feedback and payload.user_feedback not in ['like', 'dislike']:
            raise HTTPException(status_code=400, detail="user_feedback must be 'like', 'dislike', or None")
        
        # Extract external_id from formatted_content.id if present
        external_id = None
        if payload.formatted_content and payload.formatted_content.get('id'):
            external_id = str(payload.formatted_content.get('id'))
        
        # Check if post already exists by external_id (indexed lookup - O(1) instead of O(n))
        existing_post = None
        if external_id:
            # First, try to extract DB ID if format is "db-{id}"
            if external_id.startswith('db-'):
                try:
                    db_id_match = int(external_id.replace('db-', ''))
                    existing_post = db.query(SocialPost).filter(SocialPost.id == db_id_match).first()
                except ValueError:
                    pass
            
            # If not found by DB ID, use indexed external_id lookup
            if not existing_post:
                existing_post = db.query(SocialPost).filter(SocialPost.external_id == external_id).first()
            
            # Fallback: If external_id column doesn't exist yet (during migration), use JSONB query
            if not existing_post:
                # PostgreSQL JSONB expression query (indexed if migration ran)
                from sqlalchemy import text
                existing_post = db.query(SocialPost).filter(
                    text("formatted_content->>'id' = :target_id")
                ).params(target_id=external_id).first()
        
        # Parse date_for to DATE type (handle both string and date)
        from datetime import date as date_type
        if isinstance(payload.date_for, str):
            try:
                date_for_obj = datetime.strptime(payload.date_for, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid date format: {payload.date_for}. Expected YYYY-MM-DD")
        else:
            date_for_obj = payload.date_for
        
        # Topic is REQUIRED - it should come from the LLM or be provided explicitly
        # No extraction logic needed - the AI returns it, or it's provided by the frontend
        if not payload.topic:
            raise HTTPException(
                status_code=400,
                detail="topic is required. Posts generated via /generate are automatically saved. "
                       "If updating an existing post, provide the topic explicitly."
            )
        
        topic = payload.topic
        
        # Normalize and hash topic
        normalized_topic = social_topic.normalize_topic(topic)
        topic_hash = social_topic.compute_topic_hash(normalized_topic)
        
        if existing_post:
            # Update existing post
            existing_post.date_for = date_for_obj
            existing_post.caption = payload.caption
            existing_post.image_prompt = payload.image_prompt
            existing_post.post_type = payload.post_type
            existing_post.status = payload.status
            existing_post.selected_product_id = payload.selected_product_id
            existing_post.formatted_content = payload.formatted_content
            existing_post.external_id = external_id  # Update external_id
            existing_post.channel = payload.channel
            existing_post.carousel_slides = payload.carousel_slides
            existing_post.needs_music = payload.needs_music
            existing_post.user_feedback = payload.user_feedback
            # Update topic fields
            existing_post.topic = normalized_topic
            existing_post.topic_hash = topic_hash
            existing_post.problem_identified = payload.problem_identified
            db.commit()
            db.refresh(existing_post)
            return {"status": "success", "id": existing_post.id, "updated": True}
        else:
            # Create new post
            new_post = SocialPost(
                date_for=date_for_obj,
                caption=payload.caption,
                image_prompt=payload.image_prompt,
                post_type=payload.post_type,
                status=payload.status,
                selected_product_id=payload.selected_product_id,
                formatted_content=payload.formatted_content,
                external_id=external_id,  # Set external_id for efficient lookups
                channel=payload.channel,
                carousel_slides=payload.carousel_slides,
                needs_music=payload.needs_music,
                user_feedback=payload.user_feedback,
                # Topic fields (CRITICAL)
                topic=normalized_topic,
                topic_hash=topic_hash,
                problem_identified=payload.problem_identified
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
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)  # Add auth
):
    """
    Agentic Generation Workflow with DB History.
    """
    # Rate limiting
    user_id = user.get("user_id", "anonymous")
    social_logging.safe_log_info(
        "[STEP 0] Starting post generation",
        user_id=user_id,
        date=payload.date,
        category=payload.category,
        has_suggested_topic=bool(payload.suggested_topic)
    )
    
    allowed, error_msg = social_rate_limit.check_rate_limit(user_id, "/generate")
    if not allowed:
        social_logging.safe_log_warning(f"[STEP 0] Rate limit exceeded", user_id=user_id)
        raise HTTPException(status_code=429, detail=error_msg)
    
    if not claude_api_key:
        social_logging.safe_log_error("[STEP 0] CLAUDE_API_KEY not configured", user_id=user_id)
        raise HTTPException(status_code=500, detail="CLAUDE_API_KEY not configured")

    client = anthropic.Client(api_key=claude_api_key)

    # --- 0. CONTEXT INIT (needed for history query) ---
    social_logging.safe_log_info("[STEP 1] Parsing date and initializing context", user_id=user_id)
    try:
        dt = datetime.strptime(payload.date, "%Y-%m-%d")
        target_date = dt.date()  # Convert to date object for proper comparison
    except ValueError:
        social_logging.safe_log_warning(f"[STEP 1] Invalid date format: {payload.date}, using today", user_id=user_id)
        dt = datetime.now()
        target_date = dt.date()
    
    # --- 0. FETCH HISTORY (BACKEND) ---
    social_logging.safe_log_info("[STEP 2] Fetching recent posts for deduplication", user_id=user_id, target_date=str(target_date))
    # Fetch last 20 posts from last 10 days to avoid repetition
    # FIXED: Use proper DATE comparison instead of string comparison
    recent_posts = social_dedupe.fetch_recent_posts(db, dt, days_back=10, limit=20)
    social_logging.safe_log_info(
        "[STEP 2] Recent posts fetched",
        user_id=user_id,
        recent_posts_count=len(recent_posts)
    )
    
    # Build comprehensive history with post_type, channel, topic, and product
    social_logging.safe_log_info("[STEP 3] Building history summary and extracting deduplication sets", user_id=user_id)
    recent_history = social_dedupe.build_history_summary(
        recent_posts,
        batch_generated_history=payload.batch_generated_history
    )
    
    # Extract deduplication info from recent posts
    (
        recent_product_ids,
        recent_categories,
        recent_channels,
        recent_topics,
        recent_topic_keywords,
        used_in_batch_ids,
        used_in_batch_categories
    ) = social_dedupe.extract_deduplication_sets(
        recent_posts,
        dedup_context=payload.dedupContext,
        used_in_batch=payload.used_in_batch
    )
    social_logging.safe_log_info(
        "[STEP 3] Deduplication sets extracted",
        user_id=user_id,
        recent_topics_count=len(recent_topics),
        recent_products_count=len(recent_product_ids),
        recent_categories_count=len(recent_categories)
    )

    # --- 1. SEASON CONTEXT ---
    social_logging.safe_log_info("[STEP 4] Loading season context and Durango context", user_id=user_id, month=dt.month)
    sales_context = get_season_context(dt)
    important_dates = str([d["name"] for d in get_nearby_dates(dt)])
    
    # Load Durango context early (needed for problem identification)
    # Use summarized version to reduce token bloat
    durango_context = social_context.load_durango_context(month=dt.month, use_summary=True)
    social_logging.safe_log_info(
        "[STEP 4] Context loaded",
        user_id=user_id,
        phase=sales_context.get('phase'),
        important_dates_count=len(get_nearby_dates(dt))
    )
    
    # Calculate variety metrics for post_type, channel, and topics
    social_logging.safe_log_info("[STEP 5] Calculating variety metrics", user_id=user_id)
    variety_metrics = social_dedupe.analyze_variety_metrics(
        recent_posts,
        batch_generated_history=payload.batch_generated_history
    )
    social_logging.safe_log_info(
        "[STEP 5] Variety metrics calculated",
        user_id=user_id,
        total_recent=variety_metrics.get("total_recent", 0),
        promo_count=variety_metrics.get("promo_count", 0)
    )
    
    # Extract metrics for use in prompts
    recent_types = variety_metrics["recent_types"]
    recent_channels = variety_metrics["recent_channels"]
    recent_topics = variety_metrics["recent_topics"]
    recent_topic_keywords = variety_metrics["recent_topic_keywords"]
    type_counter = Counter(variety_metrics["recent_types"])
    channel_counts = variety_metrics["channel_counts"]
    topic_counts = variety_metrics["topic_counts"]
    promo_count = variety_metrics["promo_count"]
    total_recent = variety_metrics["total_recent"]
    penalize_promo = variety_metrics["penalize_promo"]
    type_repetition_warning = variety_metrics["type_repetition_warning"]
    over_focus_warning = variety_metrics["over_focus_warning"]
    calefaccion_count = variety_metrics["calefaccion_count"]
    heladas_count = variety_metrics["heladas_count"]
    invernadero_count = variety_metrics["invernadero_count"]
    mantenimiento_count = variety_metrics["mantenimiento_count"]
    
    # Identify real problems first
    social_logging.safe_log_info("[STEP 6] Identifying agricultural problems", user_id=user_id)
    nearby_dates_list = get_nearby_dates(dt)
    problems_data = identify_agricultural_problems(
        dt.month,
        sales_context['phase'],
        nearby_dates_list,
        durango_context
    )
    social_logging.safe_log_info(
        "[STEP 6] Problems identified",
        user_id=user_id,
        urgent_count=len(problems_data.get("most_urgent", [])),
        high_priority_count=len(problems_data.get("high_priority", []))
    )
    
    # Build problem-focused strategy prompt
    social_logging.safe_log_info("[STEP 7] Building strategy prompt", user_id=user_id)
    strategy_prompt = f"ACTÚA COMO: Ingeniero Agrónomo Experto con 15+ años en campo Durango.\n"
    strategy_prompt += f"Tu trabajo diario es VISITAR PARCELAS, IDENTIFICAR PROBLEMAS REALES y SOLUCIONARLOS.\n\n"
    strategy_prompt += f"FECHA: {payload.date}\n"
    strategy_prompt += f"FASE AGRÍCOLA: {sales_context['phase']} ({sales_context['name']})\n"
    strategy_prompt += f"CONTEXTO REGIONAL: {durango_context[:500]}...\n\n"
    
    # Add urgent problems (but de-emphasize to encourage variety)
    if problems_data["most_urgent"]:
        strategy_prompt += "🔴 PROBLEMAS CRÍTICOS (URGENTE - RESOLVER HOY):\n"
        strategy_prompt += "⚠️ IMPORTANTE: Estos son problemas importantes, pero NO estás obligado a elegirlos.\n"
        strategy_prompt += "Puedes elegir CUALQUIER problema agrícola relevante - no solo los de esta lista.\n"
        strategy_prompt += "VARÍA los temas: si ya generaste posts sobre estos problemas, elige algo DIFERENTE.\n\n"
        for i, prob in enumerate(problems_data["most_urgent"][:2], 1):  # Show only 2, not 3
            strategy_prompt += f"""
{i}. PROBLEMA: {prob['problem']}
   Impacto: {prob.get('impact', 'N/A')}
   Categoría: {prob.get('solution_category', 'general')}
"""
        strategy_prompt += "\n💡 Considera estos problemas, pero PRIORIZA VARIEDAD sobre repetir temas similares.\n\n"

    if problems_data["high_priority"]:
        strategy_prompt += "🟡 PROBLEMAS DE ALTA PRIORIDAD:\n"
        for i, prob in enumerate(problems_data["high_priority"][:3], 1):
            strategy_prompt += f"""
{i}. {prob['problem']}
   Impacto: {prob.get('impact', 'N/A')}
   Categoría: {prob.get('solution_category', 'general')}
"""
        strategy_prompt += "\n"
    
    strategy_prompt += f"EFEMÉRIDES: {important_dates}.\n"
    strategy_prompt += f"PREFERENCIA USUARIO: {payload.category or 'Ninguna - Genera contenido educativo valioso sobre cualquier tema agrícola relevante'}.\n"
    strategy_prompt += "⚠️ IMPORTANTE: Si no hay preferencia de categoría, NO estás limitado a productos.\n"
    strategy_prompt += "Puedes generar contenido educativo sobre CUALQUIER tema agrícola valioso (técnicas, gestión, planificación, etc.).\n\n"
    
    strategy_prompt += "TU MENTALIDAD COMO INGENIERO EXPERTO:\n\n"
    strategy_prompt += "1. VARIEDAD PRIMERO - REVISAR HISTORIAL ANTES DE DECIDIR\n"
    strategy_prompt += "   - PRIMERO: Lee el historial reciente arriba y identifica qué temas ya cubriste\n"
    strategy_prompt += "   - SEGUNDO: Elige un tema COMPLETAMENTE DIFERENTE a los temas recientes\n"
    strategy_prompt += "   - TERCERO: Identifica un problema relevante para ese tema nuevo\n"
    strategy_prompt += "   - REGLA DE ORO: Si los últimos 2-3 posts son sobre 'X', elige algo sobre 'Y' (diferente)\n"
    strategy_prompt += "   - La VARIEDAD es más importante que seguir exactamente la fase agrícola\n\n"
    strategy_prompt += "2. PROBLEMA PRIMERO, PRODUCTO DESPUÉS\n"
    strategy_prompt += "   - NO pienses '¿Qué producto promociono hoy?'\n"
    strategy_prompt += "   - SÍ piensa '¿Qué problema real está enfrentando el agricultor HOY?'\n"
    strategy_prompt += "   - Luego: '¿Qué solución técnica resuelve este problema?'\n\n"
    strategy_prompt += "3. IDENTIFICA SÍNTOMAS, NO SOLO PROBLEMAS\n"
    strategy_prompt += "   - Los agricultores ven síntomas (hojas amarillas, plantas muertas)\n"
    strategy_prompt += "   - Tú como experto identificas la causa raíz\n"
    strategy_prompt += "   - El contenido debe conectar síntoma → causa → solución\n\n"
    strategy_prompt += "4. ERRORES COMUNES SON OPORTUNIDADES DE EDUCACIÓN\n"
    strategy_prompt += "   - Si un error común causa el problema, edúcales sobre cómo evitarlo\n"
    strategy_prompt += "   - Ejemplo: 'Error común: No proteger charolas → Solución: Sistema antiheladas'\n\n"
    strategy_prompt += "5. IMPACTO MEDIBLE GENERA URGENCIA\n"
    strategy_prompt += "   - 'Pérdida 30-50% de germinación' es más urgente que 'mejora la germinación'\n"
    strategy_prompt += "   - Usa números concretos del impacto del problema\n\n"
    strategy_prompt += "6. VENTANA DE TIEMPO CREA URGENCIA\n"
    strategy_prompt += "   - 'Enero-Febrero' es más urgente que 'durante el año'\n"
    strategy_prompt += "   - Si estamos en la ventana, el problema es INMEDIATO\n\n"
    
    # Add suggested topic if provided
    if payload.suggested_topic:
        strategy_prompt += f"💡 TEMA SUGERIDO POR EL USUARIO: {payload.suggested_topic}\n⚠️ USA ESTE TEMA COMO BASE, pero puedes adaptarlo o expandirlo según sea necesario.\n\n"
    
    # Add over focus warning if present
    if over_focus_warning:
        strategy_prompt += f"{over_focus_warning}\n"
    
    # Continue building the prompt - PUT HISTORY FIRST so AI considers it before deciding
    strategy_prompt += "HISTORIAL RECIENTE (TUS ÚLTIMAS DECISIONES - REVISA ESTO PRIMERO):\n"
    strategy_prompt += f"- {recent_history or 'Sin historial previo.'}\n\n"
    strategy_prompt += "⚠️⚠️⚠️ CRÍTICO - LEE EL HISTORIAL ARRIBA ANTES DE DECIDIR ⚠️⚠️⚠️:\n"
    strategy_prompt += "1. Revisa los temas recientes en el historial\n"
    strategy_prompt += "2. Identifica qué temas/áreas ya cubriste (ej: germinación, riego, planificación)\n"
    strategy_prompt += "3. Elige un tema COMPLETAMENTE DIFERENTE a los temas recientes\n"
    strategy_prompt += "4. Si los últimos 2-3 posts son sobre 'X', elige algo sobre 'Y' (diferente área)\n"
    strategy_prompt += "5. Ejemplos de variedad:\n"
    strategy_prompt += "   - Si últimos posts: germinación/vivero → Elige: planificación/gestión/costos\n"
    strategy_prompt += "   - Si últimos posts: riego → Elige: organización/inventario/ROI\n"
    strategy_prompt += "   - Si últimos posts: planificación → Elige: técnicas/prácticas/casos de éxito\n"
    strategy_prompt += "   - Si últimos posts: productos específicos → Elige: educación general/gestión\n"
    strategy_prompt += "La VARIEDAD es más importante que seguir exactamente la fase agrícola.\n\n"
    
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

    strategy_prompt += "FORMATO DE TEMA (OBLIGATORIO):\n"
    strategy_prompt += "El tema DEBE seguir este formato: 'Problema → Solución'\n\n"
    strategy_prompt += "Ejemplos CORRECTOS:\n"
    strategy_prompt += "- 'Heladas queman plántulas → Protección con sistemas antiheladas'\n"
    strategy_prompt += "- 'Riego por surco desperdicia 70% agua → Riego por goteo eficiente'\n"
    strategy_prompt += "- 'Sustrato seco causa germinación desigual → Técnica correcta de hidratación'\n"
    strategy_prompt += "- 'Calor intenso reduce producción 30% → Mallasombra para protección'\n\n"
    strategy_prompt += "Ejemplos INCORRECTOS (evitar):\n"
    strategy_prompt += "- 'Sistemas de riego' (genérico, no identifica problema)\n"
    strategy_prompt += "- 'Productos agrícolas' (no es problema)\n"
    strategy_prompt += "- 'Mejora tu cultivo' (vago, no específico)\n\n"
    strategy_prompt += "TU TAREA:\n"
    strategy_prompt += "1. Identifica el PROBLEMA MÁS URGENTE de la lista arriba (o uno relacionado)\n"
    strategy_prompt += "2. Formula el tema como 'Problema → Solución'\n"
    strategy_prompt += "3. Elige el tipo de post que mejor comunique la solución\n"
    strategy_prompt += "4. Selecciona categoría de producto que resuelve el problema (o vacío si no aplica)\n\n"
    strategy_prompt += "⚠️⚠️⚠️ IMPORTANTE SOBRE TEMAS (CRÍTICO) ⚠️⚠️⚠️:\n"
    strategy_prompt += "- Los temas NO están limitados a categorías de productos que vendemos.\n"
    strategy_prompt += "- El objetivo es generar contenido VALIOSO para agricultores, no solo promocionar productos.\n"
    strategy_prompt += "- Puedes elegir CUALQUIER tema agrícola relevante que proporcione valor educativo:\n"
    strategy_prompt += "  * Técnicas agrícolas (preparación de suelo, rotación de cultivos, etc.)\n"
    strategy_prompt += "  * Gestión y planificación (inventario, costos, ROI, organización)\n"
    strategy_prompt += "  * Educación general (fertilización, riego, plagas, enfermedades)\n"
    strategy_prompt += "  * Casos de éxito y resultados\n"
    strategy_prompt += "  * Tendencias y tecnología agrícola\n"
    strategy_prompt += "  * Problemas comunes y soluciones\n"
    strategy_prompt += "  * Preparación para ciclos futuros\n"
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
    strategy_prompt += "- Considera que en diciembre también se prepara para el ciclo primavera-verano.\n"
    strategy_prompt += "- 'preferred_category' es SOLO para selección de productos (si aplica), NO limita el tema del contenido.\n"
    strategy_prompt += "- Puedes generar contenido educativo SIN producto asociado si el tema lo requiere.\n\n"
    strategy_prompt += "RESPONDE SOLO CON EL JSON:\n"
    strategy_prompt += "{\n"
    strategy_prompt += '  "problem_identified": "Descripción del problema real que el agricultor enfrenta HOY",\n'
    strategy_prompt += '  "topic": "Problema → Solución (formato exacto como en ejemplos) - DEBE SER DIFERENTE a temas recientes",\n'
    strategy_prompt += '  "post_type": "Escribe EXACTAMENTE el nombre del tipo (ej. Infografías, Memes/tips rápidos, Kits, etc.)",\n'
    strategy_prompt += '  "channel": "wa-status|wa-broadcast|fb-post|fb-reel|ig-post|ig-reel|tiktok (elige uno, DIFERENTE al usado ayer)",\n'
    strategy_prompt += '  "preferred_category": "Categoría de producto preferida SOLO si el tema requiere un producto específico (ej. riego, mallasombra). Si el tema es educativo general sin producto, deja vacío",\n'
    strategy_prompt += '  "search_needed": true/false (true solo si necesitas buscar un producto para el tema, false si el contenido es educativo general sin producto),\n'
    strategy_prompt += '  "search_keywords": "términos de búsqueda para embeddings SOLO si search_needed=true (ej. arado, fertilizante inicio, protección heladas). Si no hay producto, deja vacío"\n'
    strategy_prompt += "}"
    
    # Use new LLM module with strict JSON parsing and retry
    # This will raise HTTPException if topic validation fails
    strat_response = social_llm.call_strategy_llm(client, strategy_prompt)
    strat_data = {
        "problem_identified": strat_response.problem_identified,
        "topic": strat_response.topic,
        "post_type": strat_response.post_type,
        "channel": strat_response.channel,
        "preferred_category": strat_response.preferred_category or "",
        "search_needed": strat_response.search_needed,
        "search_keywords": strat_response.search_keywords or ""
    }
    
    # Check for topic duplicate (HARD RULE: no same topic_hash within 10 days)
    social_logging.safe_log_info("[STEP 9] Checking topic duplicate (hard rule)", user_id=user_id)
    is_duplicate, existing_post = social_dedupe.check_topic_duplicate(
        db,
        strat_data["topic"],
        target_date,
        days_back=10
    )
    if is_duplicate:
        social_logging.safe_log_warning(
            "[STEP 9] Topic duplicate detected (hard rule)",
            topic=strat_data['topic'],
            existing_post_id=existing_post.id if existing_post else None,
            user_id=user_id
        )
        raise HTTPException(
            status_code=409,
            detail=f"Topic already used recently: '{strat_data['topic']}'. Please choose a different topic."
        )
    social_logging.safe_log_info("[STEP 9] Topic duplicate check passed", user_id=user_id)
    
    # Check for problem duplicate (SOFT RULE: same problem with different solution within 3 days)
    social_logging.safe_log_info("[STEP 10] Checking problem duplicate (soft rule)", user_id=user_id)
    is_problem_dup, existing_problem_post = social_dedupe.check_problem_duplicate(
        db,
        strat_data["topic"],
        target_date,
        days_back=3
    )
    if is_problem_dup:
        social_logging.safe_log_warning(
            "[STEP 10] Problem duplicate detected (soft rule)",
            topic=strat_data['topic'],
            existing_post_id=existing_problem_post.id if existing_problem_post else None,
            user_id=user_id
        )
        raise HTTPException(
            status_code=409,
            detail=f"Similar problem already addressed recently. Please choose a different problem or wait a few days."
        )
    social_logging.safe_log_info("[STEP 10] Problem duplicate check passed", user_id=user_id)

    # --- 3. PRODUCT SELECTION PHASE (using embeddings) ---
    social_logging.safe_log_info(
        "[STEP 11] Starting product selection",
        user_id=user_id,
        search_needed=strat_data.get("search_needed", False)
    )
    selected_product_id = None
    selected_category = None
    product_details = None
    
    if strat_data.get("search_needed"):
        search_query = strat_data.get("search_keywords", "") or strat_data.get("topic", "")
        preferred_category = strat_data.get("preferred_category", "")
        
        # Use new product selection module
        try:
            selected_product_id, selected_category, product_details = social_products.select_product_for_post(
                db,
                search_query,
                preferred_category=preferred_category if preferred_category else None,
                recent_product_ids=recent_product_ids,
                recent_categories=recent_categories,
                used_in_batch_ids=used_in_batch_ids,
                used_in_batch_categories=used_in_batch_categories
            )
            social_logging.safe_log_info(
                "[STEP 11] Product selected",
                user_id=user_id,
                product_id=selected_product_id,
                category=selected_category
            )
        except Exception as e:
            social_logging.safe_log_error("[STEP 11] Product selection failed", exc_info=True, user_id=user_id, error=str(e))
            # Continue without product - content can be educational without specific product
    else:
        social_logging.safe_log_info("[STEP 11] Product selection skipped (search_needed=false)", user_id=user_id)

    # --- 4. CONTENT GENERATION PHASE ---
    social_logging.safe_log_info("[STEP 12] Starting content generation phase", user_id=user_id)
    # Fetch selected product details if a product was selected
    selected_product_info = ""
    if selected_product_id:
        social_logging.safe_log_info("[STEP 12] Fetching product details", user_id=user_id, product_id=selected_product_id)
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
                social_logging.safe_log_info("[STEP 12] Product details fetched", user_id=user_id, product_name=product_name if 'product_name' in locals() else None)
        except Exception as e:
            social_logging.safe_log_error("[STEP 12] Error fetching product details", exc_info=True, user_id=user_id, error=str(e))
            selected_product_info = f"\nProducto seleccionado ID: {selected_product_id}\n"
    else:
        social_logging.safe_log_info("[STEP 12] No product selected, skipping product details", user_id=user_id)

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
    
    # durango_context already loaded earlier for problem identification

    social_logging.safe_log_info("[STEP 12] Building content generation prompt", user_id=user_id)
    creation_prompt = (
        f"ACTÚA COMO: Social Media Manager especializado en contenido agrícola.\n\n"
        f"ESTRATEGIA DEFINIDA:\n"
        f"- TEMA: {strat_data.get('topic')}\n"
        f"- PROBLEMA IDENTIFICADO: {strat_data.get('problem_identified', '')}\n"
        f"- TIPO DE POST: {strat_data.get('post_type')}\n"
        f"- CANAL: {strat_data.get('channel')}\n"
        f"{selected_product_info}\n"
        f"CONTEXTO REGIONAL DURANGO (USA ESTA INFORMACIÓN PARA CONTENIDO RELEVANTE, PERO NO TE LIMITES SOLO A ESTO):\n"
        f"{durango_context}\n\n"
        f"⚠️ NOTA SOBRE EL CONTEXTO: El contexto de Durango menciona actividades estacionales, pero NO debes limitarte solo a esos temas.\n"
        f"Puedes hablar de otros temas relevantes como planificación, optimización, educación, casos de éxito, etc.\n\n"
        f"{dedup_info}\n"
        
        f"{CHANNEL_FORMATS}\n\n"
        
        "--- GUÍAS PARA CONTENIDO EDUCATIVO DE ALTO IMPACTO ---\n\n"
        "ESTRUCTURA VISUAL REQUERIDA (según tipo de post):\n\n"
        "1. INFOGRAFÍA COMPARATIVA (si tema incluye 'vs', 'comparar', 'tradicional vs'):\n"
        "   - Panel izquierdo (40%): Problema/Método antiguo (fondo naranja/rojo)\n"
        "   - Panel derecho (40%): Solución/Método mejorado (fondo verde)\n"
        "   - Sección inferior (20%): Tabla comparativa con especificaciones\n"
        "   - Código de colores: Naranja/Rojo (problema), Verde (solución)\n\n"
        "2. INFOGRAFÍA TUTORIAL (si tema incluye 'paso', 'cómo', 'instalación'):\n"
        "   - Título principal (20%): Nombre del proceso\n"
        "   - 4-6 pasos numerados (60%): Cada paso con número grande, ilustración, descripción\n"
        "   - Tips destacados (20%): Caja azul con borde verde\n\n"
        "3. INFOGRAFÍA DE SISTEMA (si tema incluye 'sistema', 'instalación completa'):\n"
        "   - Vista superior (40%): Sistema en contexto agrícola\n"
        "   - Vista en corte (40%): Componentes técnicos y flujos subterráneos\n"
        "   - Tabla especificaciones (20%): Materiales, dimensiones, capacidades\n\n"
        "4. INFOGRAFÍA MULTI-PANEL (default para infografías educativas):\n"
        "   - Panel 1 (25%): Título + Concepto principal\n"
        "   - Panel 2 (20%): Problema/Necesidad (si aplica)\n"
        "   - Panel 3 (20%): Solución/Método\n"
        "   - Panel 4 (20%): Especificaciones técnicas (tabla/lista)\n"
        "   - Panel 5 (15%): Tips/Beneficios destacados\n\n"
        "REQUISITOS TÉCNICOS OBLIGATORIOS:\n"
        "- Medidas específicas: SIEMPRE usar números exactos ('10-20 cm' no 'profundidad adecuada')\n"
        "- Porcentajes concretos: SIEMPRE usar números ('70% ahorro' no 'ahorro significativo')\n"
        "- Código de colores: Verde (bueno), Amarillo (atención), Rojo (problema), Naranja (necesita acción)\n"
        "- Tips: SIEMPRE en caja destacada (fondo azul claro, borde verde, icono 💡)\n\n"
        
        "INSTRUCCIONES:\n"
        "1. El producto ya fue seleccionado en la fase anterior. Usa la información del producto proporcionada arriba.\n"
        "2. Si NO hay producto seleccionado, crea un post educativo/valioso sobre el tema.\n"
        "   ⚠️ Esto es PERFECTO y DESEADO - no todos los posts necesitan un producto.\n"
        "   El contenido educativo general (técnicas, gestión, planificación) es muy valioso.\n"
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
    )
    
    # Detect structure type based on topic (before building image prompt section)
    topic_lower = strat_data.get('topic', '').lower()
    post_type_lower = strat_data.get('post_type', '').lower()
    
    if "compar" in topic_lower or " vs " in topic_lower or "tradicional" in topic_lower:
        structure_type = "COMPARATIVA"
        structure_guide = """
ESTRUCTURA: Comparativa lado a lado (Problema → Solución)
- Panel izquierdo (40% espacio, fondo naranja/rojo): [MÉTODO TRADICIONAL/PROBLEMA]
  * Título grande: "[MÉTODO TRADICIONAL]" (texto blanco, bold)
  * Indicador numérico grande: "[X% pérdida/problema]" (número 120px, color rojo)
  * 3-4 problemas específicos con porcentajes/datos
  * Iconos de pérdida/riesgo (rojos)
  * Flechas rojas hacia abajo
- Panel derecho (40% espacio, fondo verde): [MÉTODO MEJORADO/SOLUCIÓN]
  * Título grande: "[MÉTODO MEJORADO]" (texto blanco, bold)
  * Indicador numérico grande: "[X% ahorro/beneficio]" (número 120px, color verde)
  * 3-4 beneficios específicos con porcentajes/datos
  * Iconos de beneficio/éxito (verdes)
  * Flechas verdes hacia arriba
- Sección inferior (20% espacio, fondo blanco): Tabla comparativa
  * Columnas: Método | Consumo | Uniformidad | Costo | ROI
  * Filas: Tradicional vs Tecnificado con datos específicos
"""
    elif "paso" in topic_lower or "cómo" in topic_lower or "instalación" in topic_lower or "tutorial" in post_type_lower:
        structure_type = "TUTORIAL"
        structure_guide = """
ESTRUCTURA: Tutorial paso a paso
- Título principal (20% altura, fondo verde/azul IMPAG): "[Nombre del Proceso]"
- 4-6 pasos numerados (60% altura, cada paso en panel separado):
  * Número grande (150px, color verde IMPAG): "1", "2", "3"...
  * Título del paso (texto bold, 60px)
  * Ilustración mostrando la acción
  * Especificación técnica (medidas exactas)
  * Indicador visual del resultado esperado
- Sección de tips (20% altura, fondo azul claro con borde verde):
  * Icono 💡 grande (40px)
  * Texto: Consejos prácticos destacados
"""
    elif "sistema" in topic_lower or "instalación completa" in topic_lower or "diagrama" in topic_lower:
        structure_type = "DIAGRAMA DE SISTEMA"
        structure_guide = """
ESTRUCTURA: Diagrama de sistema técnico
- Vista superior (50% espacio): Sistema completo en paisaje agrícola Durango
- Vista en corte (50% espacio): Sección técnica mostrando:
  * Componentes subterráneos visibles
  * Flujos con flechas de color (azul=agua, verde=nutrientes, naranja=energía)
  * Dimensiones específicas etiquetadas (ej: "30-50 cm", "1-4 m")
  * Materiales y conexiones visibles
- Tabla de especificaciones (inferior): Materiales, dimensiones, capacidades
"""
    else:
        structure_type = "MULTI-PANEL"
        structure_guide = """
ESTRUCTURA: Infografía educativa multi-panel
- Panel 1 (25% altura): Título + Concepto principal (visual grande)
- Panel 2 (20% altura): Problema/Necesidad (si aplica, fondo amarillo/naranja)
- Panel 3 (20% altura): Solución/Método (fondo verde)
- Panel 4 (20% altura): Especificaciones técnicas (tabla/lista con medidas específicas)
- Panel 5 (15% altura): Tips/Beneficios destacados (caja azul con borde verde)
"""
    
    # Continue building creation_prompt with structure detection
    creation_prompt += (
        "--- INSTRUCCIONES ESPECÍFICAS PARA image_prompt ---\n"
        f"ESTRUCTURA DETECTADA: {structure_type}\n"
        f"{structure_guide}\n\n"
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
        "⚠️⚠️⚠️ REGLA CRÍTICA PARA image_prompt ⚠️⚠️⚠️:\n"
        "- Si el post NO es carrusel (no tiene carousel_slides), DEBES proporcionar 'image_prompt' (NO puede ser null ni vacío)\n"
        "- Si el post ES carrusel (tiene carousel_slides), entonces 'image_prompt' puede ser null\n"
        "- Para wa-status, stories, tiktok (sin carrusel), fb-post, ig-post: SIEMPRE proporciona 'image_prompt'\n"
        "- El 'image_prompt' DEBE ser detallado, técnico, y seguir el formato IMPAG especificado arriba\n"
        "- NUNCA dejes 'image_prompt' vacío o null a menos que sea un carrusel\n\n"
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
        f'  "topic": "{strat_data.get("topic")}",\n'
        '  "caption": "... (RESPETA: wa-status/stories/tiktok/reels = MUY CORTO, fb-post = puede ser largo)",\n'
        '  "image_prompt": "PROMPT DETALLADO OBLIGATORIO para generación de imagen (SOLO si NO es carrusel). Para stories/status debe ser autoexplicativa con texto grande visible. SIEMPRE incluye logos IMPAG y dimensiones correctas (1080×1920 para vertical, 1080×1080 para cuadrado).",\n'
        '  "carousel_slides": ["Slide 1 CON TEXTO GRANDE...", "Slide 2 CON TEXTO...", ...] (SOLO si es carrusel: TikTok 2-3, FB/IG 2-10. Si usas carousel_slides, image_prompt debe ser null),\n'
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

    # Use new LLM module with strict JSON parsing and retry
    # This will raise HTTPException on failure (no silent fallback)
    social_logging.safe_log_info("[STEP 13] Calling content LLM", user_id=user_id)
    content_response = social_llm.call_content_llm(client, creation_prompt)
    social_logging.safe_log_info(
        "[STEP 13] Content LLM response received",
        user_id=user_id,
        has_caption=bool(content_response.caption),
        has_image_prompt=bool(content_response.image_prompt),
        has_carousel=bool(content_response.carousel_slides)
    )
    
    # Verify topic matches strategy phase (content phase must echo same topic)
    content_topic = content_response.topic or strat_data.get("topic", "")
    if content_topic != strat_data.get("topic", ""):
        social_logging.safe_log_warning(
            f"Topic mismatch: strategy={strat_data.get('topic')}, content={content_topic}",
            user_id=user_id
        )
        # Use strategy topic (canonical)
        content_topic = strat_data.get("topic", "")
    
    # Validate that image_prompt is provided for non-carousel posts
    is_carousel = bool(content_response.carousel_slides and len(content_response.carousel_slides) > 0)
    if not is_carousel and not content_response.image_prompt:
        social_logging.safe_log_warning(
            "[STEP 13] Missing image_prompt for non-carousel post",
            user_id=user_id,
            channel=content_response.channel,
            post_type=strat_data.get("post_type")
        )
        # This is a critical error - non-carousel posts MUST have image_prompt
        raise HTTPException(
            status_code=500,
            detail="LLM failed to generate image_prompt for non-carousel post. This is required."
        )
    
    data = {
        "selected_category": content_response.selected_category or "",
        "selected_product_id": content_response.selected_product_id or "",
        "channel": content_response.channel,
        "caption": content_response.caption,
        "image_prompt": content_response.image_prompt if not is_carousel else None,  # Only set if not carousel
        "carousel_slides": content_response.carousel_slides,
        "needs_music": content_response.needs_music,
        "posting_time": content_response.posting_time,
        "notes": content_response.notes or "",
        "topic": content_topic  # Use canonical topic from strategy phase
    }

    # Product details already fetched in product selection phase
    # Use product_details from selection phase if available

    # Include problem_identified in notes if available
    notes_with_problem = data.get("notes", "")
    if strat_data.get("problem_identified"):
        problem_note = f"Problema identificado: {strat_data.get('problem_identified')}"
        notes_with_problem = f"{problem_note}\n\n{notes_with_problem}" if notes_with_problem else problem_note

    # Record successful request for rate limiting
    social_rate_limit.record_request(user_id, "/generate")
    
    # AUTOMATICALLY SAVE THE POST (backend has all the data, no need for frontend to send it back)
    # Normalize and hash topic
    canonical_topic = data.get("topic", strat_data.get("topic", ""))
    if not canonical_topic:
        canonical_topic = "sin tema → sin solución"
        social_logging.safe_log_warning(
            f"No topic in generate response, using placeholder",
            user_id=user_id
        )
    
    normalized_topic = social_topic.normalize_topic(canonical_topic)
    topic_hash = social_topic.compute_topic_hash(normalized_topic)
    
    # Build formatted_content for storage
    formatted_content = {
        "id": None,  # Will be set after save
        "postType": strat_data.get("post_type"),
        "channels": [strat_data.get("channel") or data.get("channel")],
        "hook": "Tendencias agrícolas",  # Default hook
        "hookType": "seasonality",
        "products": [product_details] if product_details else [],
        "tags": [],
        "instructions": notes_with_problem,
        "postingTime": data.get("posting_time"),
        "generationSource": "llm",
        "strategyNotes": notes_with_problem,
        "carouselSlides": data.get("carousel_slides"),
        "needsMusic": data.get("needs_music", False),
        "generatedContext": {
            "monthPhase": "germinacion",  # Default, can be enhanced later if needed
            "nearbyDates": [],
            "selectedCategories": [selected_category] if selected_category else []
        }
    }
    
    # Check if post already exists by topic_hash and date (avoid duplicates)
    social_logging.safe_log_info("[STEP 15] Checking for existing post by topic_hash", user_id=user_id)
    existing_post = db.query(SocialPost).filter(
        SocialPost.topic_hash == topic_hash,
        SocialPost.date_for == target_date
    ).first()
    
    if existing_post:
        # Update existing post with new content
        social_logging.safe_log_info(
            "[STEP 15] Existing post found, updating",
            user_id=user_id,
            existing_post_id=existing_post.id
        )
        existing_post.caption = data.get("caption", "")
        existing_post.image_prompt = data.get("image_prompt")
        existing_post.post_type = strat_data.get("post_type")
        existing_post.selected_product_id = selected_product_id or data.get("selected_product_id")
        existing_post.channel = strat_data.get("channel") or data.get("channel")
        existing_post.carousel_slides = data.get("carousel_slides")
        existing_post.needs_music = data.get("needs_music", False)
        existing_post.formatted_content = formatted_content
        existing_post.topic = normalized_topic
        existing_post.topic_hash = topic_hash
        existing_post.problem_identified = strat_data.get("problem_identified", "")
        db.commit()
        db.refresh(existing_post)
        saved_post_id = existing_post.id
        social_logging.safe_log_info(
            "[STEP 15] Post updated successfully",
            user_id=user_id,
            post_id=saved_post_id
        )
    else:
        # Create new post
        social_logging.safe_log_info("[STEP 15] Creating new post", user_id=user_id)
        new_post = SocialPost(
            date_for=target_date,
            caption=data.get("caption", ""),
            image_prompt=data.get("image_prompt"),
            post_type=strat_data.get("post_type"),
            status="planned",
            selected_product_id=selected_product_id or data.get("selected_product_id"),
            formatted_content=formatted_content,
            channel=strat_data.get("channel") or data.get("channel"),
            carousel_slides=data.get("carousel_slides"),
            needs_music=data.get("needs_music", False),
            topic=normalized_topic,
            topic_hash=topic_hash,
            problem_identified=strat_data.get("problem_identified", "")
        )
        db.add(new_post)
        db.commit()
        db.refresh(new_post)
        saved_post_id = new_post.id
        # Update formatted_content with actual ID
        formatted_content["id"] = str(saved_post_id)
        new_post.formatted_content = formatted_content
        db.commit()
        social_logging.safe_log_info(
            "[STEP 15] Post created successfully",
            user_id=user_id,
            post_id=saved_post_id
        )
    
    return SocialGenResponse(
        caption=data.get("caption", ""),
        image_prompt=data.get("image_prompt") or None,  # Allow None for carousel posts
        posting_time=data.get("posting_time"),
        notes=notes_with_problem,
        format=data.get("format"),
        cta=data.get("cta"),
        selected_product_id=selected_product_id or str(data.get("selected_product_id", "")),  # Use from product selection phase
        selected_category=selected_category or data.get("selected_category"),  # Use from product selection phase
        selected_product_details=product_details,  # From product selection phase
        post_type=strat_data.get("post_type"),  # From strategy phase
        channel=strat_data.get("channel") or data.get("channel"),  # From strategy phase, fallback to content phase
        carousel_slides=data.get("carousel_slides"),
        needs_music=data.get("needs_music"),
        topic=canonical_topic,  # Canonical topic from strategy phase
        problem_identified=strat_data.get("problem_identified", ""),  # From strategy phase
        saved_post_id=saved_post_id  # Return the saved post ID
    )


@router.post("/generate-batch", response_model=SocialGenBatchResponse)
async def generate_social_batch(
    payload: SocialGenBatchRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Generate multiple social posts for a date in a single batch.
    Handles category selection, batch tracking, and deduplication internally.
    """
    import random
    
    user_id = user.get("user_id", "anonymous")
    
    # Rate limiting
    allowed, error_msg = social_rate_limit.check_rate_limit(user_id, "/generate-batch")
    if not allowed:
        raise HTTPException(status_code=429, detail=error_msg)
    
    # Parse date
    try:
        dt = datetime.strptime(payload.date, "%Y-%m-%d")
        target_date = dt.date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {payload.date}. Expected YYYY-MM-DD")
    
    # Get season context and important dates
    sales_context = get_season_context(dt)
    nearby_dates_list = get_nearby_dates(dt)
    
    # Fetch recent posts for deduplication and category selection
    recent_posts = social_dedupe.fetch_recent_posts(db, dt, days_back=10, limit=20)
    
    # Select categories (backend logic - moved from frontend)
    selected_categories = _select_categories_backend(
        dt.month,
        nearby_dates_list,
        recent_posts,
        payload.category_override
    )
    
    # Determine count
    count = random.randint(payload.min_count, payload.max_count)
    
    # Internal batch tracking
    used_in_batch = {
        "product_ids": set(),
        "categories": set()
    }
    batch_generated_history = []
    
    # Generate posts
    generated_posts = []
    for i in range(count):
        # 60% chance to use a category, 40% for general educational content
        use_category = random.random() < 0.6
        preferred_category = None
        if use_category and selected_categories:
            preferred_category = selected_categories[i % len(selected_categories)]
        
        # Build request for single post generation
        single_request = SocialGenRequest(
            date=payload.date,
            category=preferred_category,
            suggested_topic=payload.suggested_topic,
            used_in_batch={
                "product_ids": list(used_in_batch["product_ids"]),
                "categories": list(used_in_batch["categories"])
            },
            batch_generated_history=batch_generated_history,
            selected_categories=selected_categories
        )
        
        try:
            # Generate single post by calling the generate function directly
            # We already have db and user, so we can call it directly (Depends is just for FastAPI injection)
            post_response = await generate_social_copy(single_request, db, user)
            generated_posts.append(post_response)
            
            # Track what we used
            if post_response.selected_product_id:
                used_in_batch["product_ids"].add(post_response.selected_product_id)
            if post_response.selected_category:
                used_in_batch["categories"].add(post_response.selected_category)
            
            # Add to batch history
            batch_generated_history.append(f"{post_response.caption[:50]}... [{post_response.post_type}]")
            
        except HTTPException as e:
            # If duplicate (409), skip this post
            if e.status_code == 409:
                social_logging.safe_log_warning(
                    f"Duplicate post detected in batch, skipping. Attempt {i+1}/{count}",
                    user_id=user_id
                )
                continue
            # Re-raise other HTTP exceptions
            raise
        except Exception as e:
            social_logging.safe_log_error(
                f"Failed to generate post {i+1}/{count} in batch: {e}",
                exc_info=True,
                user_id=user_id
            )
            # Continue with other posts even if one fails
            continue
    
    # Record successful batch request
    social_rate_limit.record_request(user_id, "/generate-batch")
    
    return SocialGenBatchResponse(
        posts=generated_posts,
        selected_categories=selected_categories,
        metadata={
            "monthPhase": sales_context["phase"],
            "monthName": sales_context["name"],
            "importantDates": [d["name"] for d in nearby_dates_list],
            "generatedCount": len(generated_posts),
            "requestedCount": count
        }
    )


def _select_categories_backend(
    month: int,
    nearby_dates: List[Dict[str, Any]],
    recent_posts: List[SocialPost],
    category_override: Optional[str] = None
) -> List[str]:
    """
    Select categories for post generation (moved from frontend).
    Uses month pattern, important dates, and recent history.
    """
    # Get month pattern categories
    month_pattern = SEASON_PATTERNS.get(month, SEASON_PATTERNS[1])
    
    # Category weights from month actions
    category_scores = {}
    
    # Map month actions to categories
    action_to_category = {
        "riego": "riego",
        "acolchado": "acolchado",
        "charolas": "charolas",
        "semillas": "vivero",
        "sustratos": "vivero",
        "anti-heladas": "antiheladas",
        "manta térmica": "antiheladas",
        "mallasombra": "mallasombra",
        "plásticos": "plasticos",
    }
    
    # Score categories based on month actions
    for action in month_pattern.get("actions", []):
        action_lower = action.lower()
        for key, cat in action_to_category.items():
            if key in action_lower:
                category_scores[cat] = category_scores.get(cat, 0) + 1.0
    
    # Boost categories from important dates (if they have relatedCategories)
    # Note: DEFAULT_DATES doesn't have relatedCategories, but we can add logic if needed
    
    # Penalize recently used categories
    recent_category_usage = Counter()
    for post in recent_posts:
        if post.formatted_content and isinstance(post.formatted_content, dict):
            products = post.formatted_content.get("products", [])
            for product in products:
                if isinstance(product, dict) and product.get("category"):
                    recent_category_usage[product["category"]] += 1
        # Also check selected_category field
        if post.selected_product_id:
            # Would need product lookup - for now skip
            pass
    
    # Apply penalty
    for cat, count in recent_category_usage.items():
        if cat in category_scores:
            category_scores[cat] *= (0.7 ** count)  # Penalty multiplier
    
    # Sort and select top 3 categories
    sorted_categories = sorted(
        category_scores.items(),
        key=lambda x: x[1],
        reverse=True
    )[:3]
    
    selected = [cat for cat, score in sorted_categories if score > 0]
    
    # If override provided, prioritize it
    if category_override:
        if category_override not in selected:
            selected.insert(0, category_override)
        else:
            # Move to front
            selected.remove(category_override)
            selected.insert(0, category_override)
    
    # Fallback to default if empty
    if not selected:
        selected = ["vivero"]  # Default category
    
    return selected[:3]  # Return max 3 categories






