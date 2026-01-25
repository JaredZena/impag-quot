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
from models import get_db, Product, ProductCategory, SocialPost, SupplierProduct
from auth import verify_google_token

# Import new modules (same directory)
import sys
from pathlib import Path
routes_dir = Path(__file__).parent
if str(routes_dir) not in sys.path:
    sys.path.insert(0, str(routes_dir))
import social_context
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
    suggested_topic: Optional[str] = None # User-suggested topic for the post

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
    content_tone: Optional[str] = None # Content tone: Motivational, Technical, Humor, Educational, Inspirational, etc.
    # Channel-specific fields
    channel: Optional[str] = None # wa-status, wa-broadcast, fb-post, fb-reel, tiktok, etc.
    carousel_slides: Optional[List[str]] = None # For TikTok carousels: list of 2-3 image prompts
    needs_music: Optional[bool] = None # Whether this content needs background music
    aspect_ratio: Optional[str] = None # 1:1, 9:16, 4:5
    # Topic-based deduplication fields (CRITICAL)
    topic: Optional[str] = None # Topic in format "Problema → Solución" (canonical unit of deduplication)
    problem_identified: Optional[str] = None # Problem description from strategy phase
    saved_post_id: Optional[int] = None # ID of the automatically saved post in database

# --- Logic ---

def get_season_context(date_obj):
    month = date_obj.month
    return SEASON_PATTERNS.get(month, SEASON_PATTERNS[1])

def identify_agricultural_problems(
    month: Optional[int],
    phase: Optional[str],
    nearby_dates: list,
    durango_context: str
) -> dict:
    """
    Returns empty structure - AI will identify problems dynamically in the strategy phase.
    This function is kept for backward compatibility but no longer returns hardcoded problems.
    
    Args:
        month: Month number (1-12) or None if seasonal context not available
        phase: Agricultural phase string or None if seasonal context not available
        nearby_dates: List of nearby dates (only populated on Fridays)
        durango_context: Regional context string
    """
    # Check nearby dates for urgency hints (e.g., heladas coming soon)
    # Only check if we have seasonal context (month and phase not None)
    urgency_hints = []
    if month is not None and phase is not None:
        for date in nearby_dates:
            if isinstance(date, dict) and date.get("type") == "seasonal":
                date_name = str(date.get("name", "")).lower()
                if "helada" in date_name or "frío" in date_name:
                    urgency_hints.append({
                        "type": "helada_risk",
                        "days_until": date.get("daysUntil", 0),
                        "name": date.get("name", "")
                    })
    
    return {
        "problems": [],
        "most_urgent": [],
        "high_priority": [],
        "month": month,
        "phase": phase,
        "urgency_hints": urgency_hints  # Pass hints to AI for context
    }

def get_nearby_dates(date_obj):
    # Simple logic: return dates in the same month
    month = date_obj.month
    return [d for d in DEFAULT_DATES if d["month"] == month]

def get_saturday_sector(dt: datetime) -> str:
    """
    Rotate sector for Saturday posts: forestry, plant, animal.
    Uses week number to determine rotation.
    """
    week_num = dt.isocalendar()[1]  # ISO week number
    sectors = ['forestry', 'plant', 'animal']
    return sectors[week_num % 3]

def get_default_tone_for_weekday(day_name: str) -> str:
    """
    Returns default content tone based on weekday theme.
    Used as fallback if LLM doesn't provide tone.
    """
    tone_map = {
        'Monday': 'Motivational',
        'Tuesday': 'Promotional',
        'Wednesday': 'Educational',
        'Thursday': 'Problem-Solving',
        'Friday': 'Seasonal',
        'Saturday': 'Educational',
        'Sunday': 'Informative'
    }
    return tone_map.get(day_name, 'Educational')

def get_weekday_theme(dt: datetime) -> Dict[str, Any]:
    """
    Returns the weekly theme and recommended post types for a given date.
    
    Returns:
        {
            'day_name': 'Monday',
            'theme': '✊ Motivational / Inspirational',
            'content_type': 'Inspiring quote or message...',
            'recommended_post_types': ['Memes/tips rápidos', 'Infografías', ...],
            'sector_rotation': None or 'forestry'|'plant'|'animal' (for Saturday)
        }
    """
    weekday = dt.weekday()  # 0=Monday, 6=Sunday
    
    themes = {
        0: {  # Monday
            'day_name': 'Monday',
            'theme': '✊ Motivational / Inspirational',
            'content_type': 'Inspiring quote or message for agriculture/forestry producers',
            'recommended_post_types': [
                'Motivational Phrase or Quote of the Week',
                'Memes/tips rápidos',
                'Image / Photo of the Week'
            ],
            'sector_rotation': None
        },
        1: {  # Tuesday
            'day_name': 'Tuesday',
            'theme': '💸 Promotion / Deals',
            'content_type': 'Highlight a product with a special price, bundle, or seasonal offer',
            'recommended_post_types': [
                'Promoción puntual',
                'Kits',
                '"Lo que llegó hoy"',
                'Cómo pedir / logística',
                'Recordatorio de servicio'
            ],
            'sector_rotation': None
        },
        2: {  # Wednesday
            'day_name': 'Wednesday',
            'theme': '📚 Educational / Tips',
            'content_type': 'Tips, guides, how-tos, or educational content for farmers',
            'recommended_post_types': [
                'Infografías de producto o tema',
                'Tutorial corto',
                'Pro Tip',
                'Interesting Fact',
                'Article',
                'Sabías que...'
            ],
            'sector_rotation': None
        },
        3: {  # Thursday
            'day_name': 'Thursday',
            'theme': '🛠️ Problem & Solution',
            'content_type': 'Infographic showing how one of your products solves a real problem',
            'recommended_post_types': [
                'Infografías',
                'Caso de éxito',
                'Antes / Después'
            ],
            'sector_rotation': None
        },
        4: {  # Friday
            'day_name': 'Friday',
            'theme': '📅 Seasonal Focus',
            'content_type': 'Advice or alerts based on regional crop/livestock/forestry seasons',
            'recommended_post_types': [
                'Infografías',
                'Tutorial corto',
                'Checklist operativo',
                'Recordatorio de servicio',
                'Seasonal weather tips: what to expect & how to act'
            ],
            'sector_rotation': None
        },
        5: {  # Saturday
            'day_name': 'Saturday',
            'theme': '👩‍🌾 Producer Segment Focus',
            'content_type': 'Rotate content for: forestry 🌲, plant 🌾, animal 🐄 producers',
            'recommended_post_types': [
                'Infografías',
                'FAQ / Mitos',
                'Pro Tip',
                'Interesting Fact',
                'Tutorial corto',
                'Recordatorio de servicio'
            ],
            'sector_rotation': get_saturday_sector(dt)  # Rotate weekly
        },
        6: {  # Sunday
            'day_name': 'Sunday',
            'theme': '📊 Innovation / Industry Reports',
            'content_type': 'Industry news, agri-innovation, or trending novelty in agriculture',
            'recommended_post_types': [
                'Industry novelty',
                'Trivia agrotech-style post',
                'Statistics or report highlights relevant to audience'
            ],
            'sector_rotation': None
        }
    }
    
    return themes[weekday]

def get_special_date_override(dt: datetime) -> Optional[Dict[str, Any]]:
    """
    Check if date matches a special date (holiday or agricultural day).
    Returns override theme if found, None otherwise.
    """
    month = dt.month
    day = dt.day
    
    # Mexican National Holidays & Social Dates
    special_dates = {
        (1, 1): {'name': 'Año Nuevo', 'type': 'holiday'},
        (2, 5): {'name': 'Día de la Constitución', 'type': 'holiday'},
        (3, 21): {'name': 'Natalicio de Benito Juárez', 'type': 'holiday'},
        (5, 10): {'name': 'Día de las Madres', 'type': 'social'},
        (5, 15): {'name': 'Día del Maestro', 'type': 'social'},
        (9, 16): {'name': 'Día de la Independencia', 'type': 'holiday'},
        (11, 2): {'name': 'Día de Muertos', 'type': 'holiday'},
        (12, 25): {'name': 'Navidad', 'type': 'holiday'},
        # Environment & Agriculture-Related Days
        (3, 22): {'name': 'Día Mundial del Agua', 'type': 'agricultural'},
        (4, 22): {'name': 'Día de la Tierra', 'type': 'agricultural'},
        (4, 15): {'name': 'Día del Agrónomo (Mexico)', 'type': 'agricultural'},
        (6, 5): {'name': 'Día Mundial del Medio Ambiente', 'type': 'agricultural'},
        (10, 16): {'name': 'Día Mundial de la Alimentación', 'type': 'agricultural'},
    }
    
    # Check for exact date match
    if (month, day) in special_dates:
        special = special_dates[(month, day)]
        return {
            'is_special_date': True,
            'special_date_name': special['name'],
            'special_date_type': special['type'],
            'override_weekday_theme': True,
            'recommended_post_type': 'Fechas importantes'
        }
    
    # Check for Día del Padre (3rd Sunday of June)
    if month == 6 and dt.weekday() == 6:  # Sunday
        week_of_month = (day - 1) // 7 + 1
        if week_of_month == 3:
            return {
                'is_special_date': True,
                'special_date_name': 'Día del Padre',
                'special_date_type': 'social',
                'override_weekday_theme': True,
                'recommended_post_type': 'Fechas importantes'
            }
    
    return None

class SocialPostSaveRequest(BaseModel):
    date_for: str
    caption: str
    image_prompt: Optional[str] = None
    post_type: Optional[str] = None
    content_tone: Optional[str] = None # Content tone: Motivational, Technical, Humor, Educational, Inspirational, etc.
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
                    "content_tone": p.content_tone,
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
                    "content_tone": p.content_tone,
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
            existing_post.content_tone = payload.content_tone
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
                content_tone=payload.content_tone,
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
    Generates ONE post per request. If multiple posts are created, check frontend for multiple calls.
    """
    # Rate limiting
    user_id = user.get("user_id", "anonymous")
    social_logging.safe_log_info(
        "[STEP 0] Starting post generation - SINGLE POST ONLY",
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
    
    # Get weekday theme early to determine what context is needed
    weekday_theme = get_weekday_theme(dt)
    is_friday = dt.weekday() == 4
    
    # --- 1. SEASON CONTEXT (ONLY on Fridays) ---
    # Only load seasonal context on Fridays to prevent seasonal content on other days
    sales_context = None
    important_dates = ""
    if is_friday:
        social_logging.safe_log_info("[STEP 4] Loading season context (Friday - seasonal content allowed)", user_id=user_id, month=dt.month)
        sales_context = get_season_context(dt)
        important_dates = str([d["name"] for d in get_nearby_dates(dt)])
    else:
        social_logging.safe_log_info("[STEP 4] Skipping season context (not Friday - seasonal content not allowed)", user_id=user_id, weekday=weekday_theme['day_name'])
    
    # Load Durango context ONLY for Thursday, Friday, Saturday (Problem/Solution, Seasonal Focus, Producer Segment Focus)
    durango_context = ""
    needs_durango_context = weekday_theme['day_name'] in ['Thursday', 'Friday', 'Saturday']
    
    if needs_durango_context:
        social_logging.safe_log_info("[STEP 4] Loading Durango context (required for weekday theme)", user_id=user_id, weekday=weekday_theme['day_name'])
        durango_context = social_context.load_durango_context(month=dt.month, use_summary=True)
    else:
        social_logging.safe_log_info("[STEP 4] Skipping Durango context (not needed for weekday theme)", user_id=user_id, weekday=weekday_theme['day_name'])
    
    social_logging.safe_log_info(
        "[STEP 4] Context loaded",
        user_id=user_id,
        phase=sales_context.get('phase') if sales_context else "N/A (not Friday)",
        important_dates_count=len(get_nearby_dates(dt)) if is_friday else 0,
        durango_context_loaded=needs_durango_context,
        seasonal_context_loaded=is_friday
    )
    
    # Identify real problems first
    # Only pass seasonal context on Fridays
    social_logging.safe_log_info("[STEP 6] Identifying agricultural problems", user_id=user_id)
    nearby_dates_list = get_nearby_dates(dt) if is_friday else []
    problems_data = identify_agricultural_problems(
        dt.month if is_friday else None,  # Don't pass month on non-Fridays
        sales_context['phase'] if sales_context else None,  # Don't pass phase on non-Fridays
        nearby_dates_list,
        durango_context if needs_durango_context else ""
    )
    social_logging.safe_log_info(
        "[STEP 6] Problems identified",
        user_id=user_id,
        urgent_count=len(problems_data.get("most_urgent", [])),
        high_priority_count=len(problems_data.get("high_priority", []))
    )
    
    # Check for special date override
    special_date = get_special_date_override(dt)
    if special_date:
        # Override recommended post type for special dates
        weekday_theme['recommended_post_types'] = [special_date['recommended_post_type']] + weekday_theme['recommended_post_types']
        social_logging.safe_log_info(
            "[PHASE 3] Special date detected",
            user_id=user_id,
            special_date=special_date['special_date_name'],
            recommended_post_type=special_date['recommended_post_type']
        )
    
    social_logging.safe_log_info(
        "[PHASE 3] Weekday theme determined",
        user_id=user_id,
        weekday=weekday_theme['day_name'],
        theme=weekday_theme['theme'],
        recommended_types=weekday_theme['recommended_post_types']
    )
    
    # Build problem-focused strategy prompt
    social_logging.safe_log_info("[STEP 7] Building strategy prompt", user_id=user_id)
    strategy_prompt = f"ACTÚA COMO: Ingeniero Agrónomo Experto con 15+ años en campo Durango.\n"
    strategy_prompt += f"Tu trabajo diario es VISITAR PARCELAS, IDENTIFICAR PROBLEMAS REALES y SOLUCIONARLOS.\n\n"
    strategy_prompt += f"FECHA: {payload.date}\n"
    # Only include seasonal/phase context on Fridays
    if is_friday and sales_context:
        strategy_prompt += f"FASE AGRÍCOLA: {sales_context['phase']} ({sales_context['name']})\n"
    
    # Only include Durango context for Thursday, Friday, Saturday
    if needs_durango_context:
        strategy_prompt += f"CONTEXTO REGIONAL DURANGO: {durango_context[:500]}...\n\n"
    else:
        strategy_prompt += "⚠️ NOTA: El contexto regional de Durango NO está disponible para este día.\n"
        strategy_prompt += "Enfócate en contenido general que no requiera conocimiento regional específico.\n\n"
    
    # Add weekday theme section
    strategy_prompt += f"📅 PLAN SEMANAL DE CONTENIDO - DÍA ACTUAL: {weekday_theme['day_name']}\n"
    strategy_prompt += f"🎯 TEMA DEL DÍA: {weekday_theme['theme']}\n"
    strategy_prompt += f"📝 TIPO DE CONTENIDO: {weekday_theme['content_type']}\n\n"
    
    if weekday_theme['sector_rotation']:
        sector_emoji = {'forestry': '🌲', 'plant': '🌾', 'animal': '🐄'}.get(weekday_theme['sector_rotation'], '')
        sector_name = {'forestry': 'Forestal', 'plant': 'Plantas/Cultivos', 'animal': 'Ganadería'}.get(weekday_theme['sector_rotation'], '')
        strategy_prompt += f"👩‍🌾 SECTOR DE ESTA SEMANA (Producer Segment Focus): {sector_emoji} {sector_name}\n"
        strategy_prompt += f"Enfócate en contenido relevante para productores de {sector_name.lower()}.\n"
        strategy_prompt += f"Ejemplos de temas:\n"
        if weekday_theme['sector_rotation'] == 'forestry':
            strategy_prompt += "- Forestal: 'Cómo almacenar agua para tus viveros forestales', 'Pro Tip: Mejores prácticas para viveros'\n"
        elif weekday_theme['sector_rotation'] == 'plant':
            strategy_prompt += "- Plantas: 'Riego eficiente con accesorios que sí duran', 'FAQ: ¿Cuándo es mejor momento para fertilizar?'\n"
        else:  # animal
            strategy_prompt += "- Ganadería: 'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado', 'Interesting Fact: El agua representa X% del costo'\n"
        strategy_prompt += "\n"
    
    if special_date:
        strategy_prompt += f"🎉 FECHA ESPECIAL: {special_date['special_date_name']}\n"
        strategy_prompt += f"⚠️ PRIORIZA contenido relacionado con esta fecha especial.\n"
        strategy_prompt += f"TIPO DE POST RECOMENDADO: {special_date['recommended_post_type']}\n"
        strategy_prompt += f"Puedes combinar el tema del día ({weekday_theme['theme']}) con la fecha especial.\n\n"
    
    strategy_prompt += "⚠️ IMPORTANTE - SIGUE EL TEMA DEL DÍA:\n"
    strategy_prompt += f"- PRIORIZA estos tipos de post para {weekday_theme['day_name']}: {', '.join(weekday_theme['recommended_post_types'])}\n"
    strategy_prompt += "- El tema del día es una GUÍA, no una restricción absoluta\n"
    strategy_prompt += "- Si el tema del día no encaja con problemas urgentes o fechas importantes, puedes adaptarlo\n"
    strategy_prompt += "- PERO: Siempre considera primero los tipos de post recomendados para el día\n\n"
    
    # Only include urgency hints and important dates on Fridays (seasonal content)
    if is_friday:
        # Add urgency hints from nearby dates (e.g., heladas coming soon)
        if problems_data.get("urgency_hints"):
            strategy_prompt += "⚠️ EVENTOS PRÓXIMOS (considera en tu análisis):\n"
            for hint in problems_data["urgency_hints"]:
                if hint.get("type") == "helada_risk":
                    days = hint.get("days_until", 0)
                    strategy_prompt += f"- {hint.get('name', 'Heladas')} en {days} días - considera problemas relacionados con protección contra frío\n"
            strategy_prompt += "\n"
        
        if important_dates:
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
    
    strategy_prompt += "TIPOS DE POST DISPONIBLES (ELIGE UNO DE ESTA LISTA - VARÍA RESPECTO A LOS ÚLTIMOS DÍAS):\n"
    strategy_prompt += f"{POST_TYPES_DEFINITIONS}\n\n"
    
    strategy_prompt += f"🎯 TIPOS DE POST RECOMENDADOS PARA {weekday_theme['day_name']} ({weekday_theme['theme']}):\n"
    strategy_prompt += "DEBES elegir UNO de estos tipos (prioriza estos sobre otros):\n"
    for post_type in weekday_theme['recommended_post_types']:
        strategy_prompt += f"- {post_type}\n"
    strategy_prompt += "\n"
    strategy_prompt += "⚠️ PRIORIZA estos tipos recomendados. Solo elige otros tipos si ninguno de los recomendados se adapta al tema específico.\n"
    strategy_prompt += f"⚠️ El tema del día ({weekday_theme['theme']}) es una GUÍA - si hay un problema urgente o fecha especial, puedes adaptarlo, pero mantén el enfoque del día.\n\n"
    
    # Special guidance for Tuesday (Promotion / Deals day)
    if weekday_theme['day_name'] == 'Tuesday':
        strategy_prompt += "💸💸💸 MARTES - DÍA DE PROMOCIONES 💸💸💸\n"
        strategy_prompt += "Hoy es MARTES (💸 Promotion / Deals). Este día SIEMPRE requiere productos.\n\n"
        strategy_prompt += "🚨 REGLA ABSOLUTA PARA MARTES - NO HAY EXCEPCIONES:\n"
        strategy_prompt += "1. 'search_needed' DEBE ser SIEMPRE 'true' (OBLIGATORIO)\n"
        strategy_prompt += "2. 'preferred_category' DEBE tener una categoría (ej: 'riego', 'fertilizantes', 'mallasombra', 'herramientas')\n"
        strategy_prompt += "3. 'search_keywords' DEBE tener términos de búsqueda (ej: 'sistema riego', 'fertilizante', 'malla sombra')\n"
        strategy_prompt += "4. El tema DEBE incluir un producto específico o promoción\n"
        strategy_prompt += "5. Los tipos de post para martes SIEMPRE requieren productos:\n"
        strategy_prompt += "   - Promoción puntual → requiere producto con oferta\n"
        strategy_prompt += "   - Kits → requiere combo de productos\n"
        strategy_prompt += "   - 'Lo que llegó hoy' → requiere producto nuevo\n"
        strategy_prompt += "   - Cómo pedir / logística → puede incluir producto\n"
        strategy_prompt += "   - Recordatorio de servicio → puede incluir producto relacionado\n\n"
        strategy_prompt += "❌ INVALIDO para martes (NUNCA hagas esto):\n"
        strategy_prompt += "- 'search_needed': false (SIEMPRE debe ser true)\n"
        strategy_prompt += "- Contenido educativo general sin producto\n"
        strategy_prompt += "- Tema que no mencione o incluya un producto\n"
        strategy_prompt += "- Dejar 'preferred_category' vacío\n"
        strategy_prompt += "- Dejar 'search_keywords' vacío\n\n"
        strategy_prompt += "✅ EJEMPLO CORRECTO para martes:\n"
        strategy_prompt += "- Tema: 'Fugas en sistema riego causan desperdicio 40% → Kit reparación con mangueras y conectores'\n"
        strategy_prompt += "- search_needed: true\n"
        strategy_prompt += "- preferred_category: 'riego'\n"
        strategy_prompt += "- search_keywords: 'manguera riego conectores kit reparación'\n"
        strategy_prompt += "- post_type: 'Kits' o 'Promoción puntual'\n\n"
    
    # Note: Seasonal context is only included on Fridays, so no explicit restriction needed
    # On non-Fridays, the LLM won't have seasonal information to work with
    if is_friday:
        # Friday - seasonal content is allowed and encouraged
        strategy_prompt += "📅 VIERNES - CONTENIDO ESTACIONAL:\n"
        strategy_prompt += "Hoy es viernes (📅 Seasonal Focus). El contexto estacional arriba (FASE AGRÍCOLA, EFEMÉRIDES) está disponible para generar contenido estacional relacionado con la época del año.\n\n"
    
    # Add content tone guidance based on weekday
    strategy_prompt += "🎨 TONO DE CONTENIDO (CONTENT TONE):\n"
    strategy_prompt += "El tono del contenido debe alinearse con el tema del día, pero puedes adaptarlo según el contexto.\n\n"
    
    # Map weekday to suggested tones
    tone_guidance = {
        'Monday': {
            'primary': 'Motivational',
            'alternatives': ['Inspirational', 'Encouraging', 'Humorous']
        },
        'Tuesday': {
            'primary': 'Promotional',
            'alternatives': ['Sales-focused', 'Urgent', 'Humorous']
        },
        'Wednesday': {
            'primary': 'Educational',
            'alternatives': ['Technical', 'Informative', 'Humorous']
        },
        'Thursday': {
            'primary': 'Problem-Solving',
            'alternatives': ['Technical', 'Solution-focused', 'Educational']
        },
        'Friday': {
            'primary': 'Seasonal',
            'alternatives': ['Educational', 'Informative', 'Technical']
        },
        'Saturday': {
            'primary': 'Educational',
            'alternatives': ['Technical', 'Practical', 'Humorous']
        },
        'Sunday': {
            'primary': 'Informative',
            'alternatives': ['Technical', 'Educational', 'Humorous']
        }
    }
    
    day_name = weekday_theme['day_name']
    if day_name in tone_guidance:
        guidance = tone_guidance[day_name]
        strategy_prompt += f"TONO RECOMENDADO PARA {day_name}:\n"
        strategy_prompt += f"- Tono principal sugerido: {guidance['primary']}\n"
        strategy_prompt += f"- Tonos alternativos: {', '.join(guidance['alternatives'])}\n"
        strategy_prompt += f"- También puedes usar: Humorous (cuando sea apropiado para el tema)\n\n"
    
    strategy_prompt += "TONOS DISPONIBLES (elige uno):\n"
    strategy_prompt += "- Motivational: Inspirador, alentador, que motive a los productores\n"
    strategy_prompt += "- Promotional: Enfocado en ventas, ofertas, productos\n"
    strategy_prompt += "- Technical: Técnico, detallado, con especificaciones y datos\n"
    strategy_prompt += "- Educational: Educativo, informativo, que enseñe algo nuevo\n"
    strategy_prompt += "- Problem-Solving: Enfocado en resolver problemas específicos\n"
    strategy_prompt += "- Seasonal: Relacionado con temporadas, ciclos, fechas importantes\n"
    strategy_prompt += "- Humorous: Divertido, ligero, con humor apropiado para agricultura\n"
    strategy_prompt += "- Informative: Informativo, noticioso, con datos y estadísticas\n"
    strategy_prompt += "- Inspirational: Inspirador, que genere emociones positivas\n"
    strategy_prompt += "\n"
    strategy_prompt += "⚠️ Elige el tono que mejor se adapte al tema, tipo de post y canal seleccionado.\n"
    strategy_prompt += "⚠️ Puedes usar Humorous cuando el tema lo permita, incluso en días técnicos.\n"
    strategy_prompt += "⚠️ El tono debe ser consistente con el tipo de post (ej: Memes/tips rápidos puede ser Humorous).\n\n"

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
    strategy_prompt += "1. IDENTIFICA un problema agrícola real y relevante para esta fecha y fase\n"
    if needs_durango_context:
        strategy_prompt += "   - Usa tu conocimiento de agricultura en Durango (revisa el contexto regional arriba)\n"
    else:
        strategy_prompt += "   - Enfócate en problemas generales de agricultura que no requieren conocimiento regional específico\n"
    # Only mention phase on Fridays
    if is_friday and sales_context:
        strategy_prompt += f"   - Considera la fase agrícola actual: {sales_context['phase']} ({sales_context['name']})\n"
        strategy_prompt += f"   - Considera el mes: {dt.month} y las condiciones estacionales típicas\n"
        strategy_prompt += "   - Piensa en problemas REALES que los agricultores enfrentan HOY en esta época del año\n"
    else:
        strategy_prompt += "   - Enfócate en problemas generales que ocurren durante todo el año (no dependientes de temporada)\n"
        strategy_prompt += "   - Piensa en problemas técnicos, de gestión, o educativos que son relevantes siempre\n"
    strategy_prompt += "   - NO uses problemas genéricos - sé específico sobre síntomas, impactos y urgencia\n"
    if is_friday:
        strategy_prompt += "   - Considera problemas de: germinación, riego, protección, planificación, costos, gestión, etc.\n"
        strategy_prompt += "   - Si hay eventos próximos (ej: heladas), considera problemas relacionados\n"
    else:
        strategy_prompt += "   - Considera problemas de: riego, fertilización, control de plagas, planificación, costos, gestión, organización, etc. (problemas que ocurren todo el año)\n"
        strategy_prompt += "   - NO menciones problemas estacionales o dependientes de época del año\n"
    strategy_prompt += "2. Formula el tema como 'Problema → Solución' (formato exacto requerido)\n"
    strategy_prompt += "   - El problema debe ser específico y medible (ej: 'Pérdida 30% de germinación' no 'germinación baja')\n"
    strategy_prompt += "   - La solución debe ser técnica y accionable\n"
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
    strategy_prompt += '  "content_tone": "Elige UNO de los tonos disponibles (Motivational, Promotional, Technical, Educational, Problem-Solving, Seasonal, Humorous, Informative, Inspirational) que mejor se adapte al tema y tipo de post",\n'
    # Special guidance for product selection based on weekday
    if weekday_theme['day_name'] == 'Tuesday':
        strategy_prompt += '  "preferred_category": "Categoría de producto OBLIGATORIA para martes (ej. riego, mallasombra, fertilizantes, herramientas, sustratos). DEBES seleccionar una categoría - NO puede estar vacío",\n'
        strategy_prompt += '  "search_needed": true (OBLIGATORIO para martes - SIEMPRE debe ser true, NUNCA false),\n'
        strategy_prompt += '  "search_keywords": "Términos de búsqueda OBLIGATORIOS para embeddings (ej. sistema riego, fertilizante, malla sombra, kit). DEBES proporcionar keywords - NO puede estar vacío"\n'
    else:
        strategy_prompt += '  "preferred_category": "Categoría de producto preferida SOLO si el tema requiere un producto específico (ej. riego, mallasombra). Si el tema es educativo general sin producto, deja vacío",\n'
        strategy_prompt += '  "search_needed": true/false (true solo si necesitas buscar un producto para el tema, false si el contenido es educativo general sin producto),\n'
        strategy_prompt += '  "search_keywords": "términos de búsqueda para embeddings SOLO si search_needed=true (ej. arado, fertilizante inicio, protección heladas). Si no hay producto, deja vacío"\n'
    strategy_prompt += "}"
    
    # Use new LLM module with strict JSON parsing and retry
    # This will raise HTTPException if topic validation fails
    strat_response = social_llm.call_strategy_llm(client, strategy_prompt)
    
    # Ensure content_tone is never empty - use fallback if needed
    content_tone = strat_response.content_tone.strip() if strat_response.content_tone else ""
    if not content_tone:
        default_tone = get_default_tone_for_weekday(weekday_theme['day_name'])
        social_logging.safe_log_warning(
            "[STEP 10] LLM returned empty content_tone, using default",
            user_id=user_id,
            default_tone=default_tone,
            weekday=weekday_theme['day_name']
        )
        content_tone = default_tone
    
    # Note: Seasonal content validation is now handled in the prompt itself
    # The prompt explicitly prohibits seasonal topics on non-Fridays
    # No retry mechanism needed - the LLM should follow the prompt instructions
    
    # Force product selection on Tuesday (Promotion / Deals day)
    search_needed = strat_response.search_needed
    search_keywords_override = strat_response.search_keywords or ""
    preferred_category_override = strat_response.preferred_category or ""
    
    if weekday_theme['day_name'] == 'Tuesday':
        if not search_needed:
            social_logging.safe_log_warning(
                "[STEP 10] LLM set search_needed=false on Tuesday - forcing to true",
                user_id=user_id,
                original_search_needed=search_needed
            )
            search_needed = True
        
        # If no preferred_category was set, try to infer from topic
        if not preferred_category_override:
            topic_lower = strat_response.topic.lower()
            if any(word in topic_lower for word in ['riego', 'agua', 'goteo', 'aspersión', 'manguera']):
                preferred_category_override = "riego"
            elif any(word in topic_lower for word in ['fertilizante', 'nutrición', 'nutriente', 'abono']):
                preferred_category_override = "fertilizantes"
            elif any(word in topic_lower for word in ['malla', 'sombra', 'protección', 'antiheladas']):
                preferred_category_override = "mallasombra"
            elif any(word in topic_lower for word in ['herramienta', 'pala', 'azadón', 'rastrillo']):
                preferred_category_override = "herramientas"
            else:
                preferred_category_override = ""  # Will be handled in product selection
            if preferred_category_override:
                social_logging.safe_log_info(
                    "[STEP 10] Inferred preferred_category for Tuesday",
                    user_id=user_id,
                    inferred_category=preferred_category_override
                )
        
        # If no search_keywords, generate from topic
        if not search_keywords_override:
            # Extract key terms from topic for product search
            topic_words = strat_response.topic.lower().split()
            # Remove common words and keep product-related terms
            stop_words = {'problema', 'solución', '→', 'de', 'la', 'el', 'en', 'con', 'para', 'por', 'un', 'una', 'los', 'las', 'del', 'al'}
            keywords = [w for w in topic_words if w not in stop_words and len(w) > 3]
            search_keywords_override = " ".join(keywords[:5])  # Take first 5 relevant words
            if search_keywords_override:
                social_logging.safe_log_info(
                    "[STEP 10] Generated search_keywords for Tuesday",
                    user_id=user_id,
                    generated_keywords=search_keywords_override
                )
    
    strat_data = {
        "problem_identified": strat_response.problem_identified,
        "topic": strat_response.topic,
        "post_type": strat_response.post_type,
        "channel": strat_response.channel,
        "content_tone": content_tone,  # Always non-empty after fallback
        "preferred_category": preferred_category_override,
        "search_needed": search_needed,  # Forced to true on Tuesday
        "search_keywords": search_keywords_override
    }
    
    # --- 3. PRODUCT SELECTION PHASE (using embeddings) ---
    social_logging.safe_log_info(
        "[STEP 11] Starting product selection",
        user_id=user_id,
        search_needed=strat_data.get("search_needed", False),
        weekday=weekday_theme['day_name']
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
                preferred_category=preferred_category if preferred_category else None
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

    # durango_context already loaded earlier for problem identification

    social_logging.safe_log_info("[STEP 12] Building content generation prompt", user_id=user_id)
    
    # Get weekday theme for content generation (already computed earlier, but ensure we have it)
    if 'weekday_theme' not in locals():
        weekday_theme = get_weekday_theme(dt)
    
    creation_prompt = (
        f"ACTÚA COMO: Social Media Manager especializado en contenido agrícola.\n\n"
        f"📅 PLAN SEMANAL - DÍA ACTUAL: {weekday_theme['day_name']}\n"
        f"🎯 TEMA DEL DÍA: {weekday_theme['theme']}\n"
        f"📝 TIPO DE CONTENIDO: {weekday_theme['content_type']}\n\n"
        
        f"ESTRATEGIA DEFINIDA:\n"
        f"- TEMA: {strat_data.get('topic')}\n"
        f"- PROBLEMA IDENTIFICADO: {strat_data.get('problem_identified', '')}\n"
        f"- TIPO DE POST: {strat_data.get('post_type')}\n"
        f"- CANAL: {strat_data.get('channel')}\n"
    )
    
    # Add sector-specific guidance for Saturday
    if weekday_theme['sector_rotation']:
        sector_emoji = {'forestry': '🌲', 'plant': '🌾', 'animal': '🐄'}.get(weekday_theme['sector_rotation'], '')
        sector_name = {'forestry': 'Forestal', 'plant': 'Plantas/Cultivos', 'animal': 'Ganadería'}.get(weekday_theme['sector_rotation'], '')
        creation_prompt += f"\n👩‍🌾 SECTOR DE ESTA SEMANA (Producer Segment Focus): {sector_emoji} {sector_name}\n"
        creation_prompt += f"Asegúrate de que el contenido sea relevante para productores de {sector_name.lower()}.\n"
        creation_prompt += f"Formatos recomendados: Infografías, FAQ/Mitos, Pro Tip, Interesting Fact, Tutorial corto, Recordatorio de servicio\n"
        creation_prompt += f"Ejemplos de contenido:\n"
        if weekday_theme['sector_rotation'] == 'forestry':
            creation_prompt += "- Forestal: 'Cómo almacenar agua para tus viveros forestales', 'Pro Tip: Mejores prácticas para viveros'\n"
        elif weekday_theme['sector_rotation'] == 'plant':
            creation_prompt += "- Plantas: 'Riego eficiente con accesorios que sí duran', 'FAQ: ¿Cuándo es mejor momento para fertilizar?'\n"
        else:  # animal
            creation_prompt += "- Ganadería: 'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado', 'Interesting Fact: El agua representa X% del costo'\n"
        creation_prompt += "\n"
    
    # Add day-specific content guidance
    if weekday_theme['day_name'] == 'Monday':
        creation_prompt += "✊ MOTIVATIONAL / INSPIRATIONAL MONDAY:\n"
        creation_prompt += "- Incluye un mensaje inspirador o motivacional relacionado con la agricultura/forestry\n"
        creation_prompt += "- Puede ser una frase motivacional, cita de la semana, meme educativo, o imagen/foto destacada\n"
        creation_prompt += "- Mantén el tono positivo y alentador\n"
        creation_prompt += "- Formatos: Motivational Phrase/Quote, Memes/tips rápidos, Image/Photo of the Week\n"
        creation_prompt += "- Ejemplos: 'El campo es trabajo y pasión', 'Cada siembra es una apuesta al futuro', Foto de campo con mensaje inspirador\n\n"
    elif weekday_theme['day_name'] == 'Tuesday':
        creation_prompt += "💸 PROMOTION / DEALS TUESDAY:\n"
        creation_prompt += "- Destaca el producto con precio especial, bundle, o oferta estacional\n"
        creation_prompt += "- Si hay producto seleccionado, enfatiza la promoción o el valor\n"
        creation_prompt += "- Incluye llamada a la acción clara para contacto/compra\n"
        creation_prompt += "- Formatos: Promoción puntual, Kits, 'Lo que llegó hoy', Cómo pedir/logística, Recordatorio de servicio\n"
        creation_prompt += "- Ejemplos: '¿Sabías que una Abrazadera Sin Fin puede ayudarte a optimizar tu sistema de riego?', 'Ofertas de temporada: Geotanques para captación de agua'\n\n"
    elif weekday_theme['day_name'] == 'Wednesday':
        creation_prompt += "📚 EDUCATIONAL / TIPS WEDNESDAY:\n"
        creation_prompt += "- Enfócate en educar: tips, guías, cómo hacer, datos interesantes, o artículos\n"
        creation_prompt += "- El contenido debe ser práctico y accionable\n"
        creation_prompt += "- Formatos: Infografías de producto o tema, Tutorial corto, Pro Tip, Interesting Fact, Article, Sabías que...\n"
        creation_prompt += "- Ejemplos: 'Cómo reducir la evaporación del agua en tus cultivos', 'Errores comunes al instalar un sistema de riego por goteo', 'Sabías que... los geotanques pueden almacenar hasta X litros'\n\n"
    elif weekday_theme['day_name'] == 'Thursday':
        creation_prompt += "🛠️ PROBLEM & SOLUTION THURSDAY:\n"
        creation_prompt += "- Muestra claramente el problema y cómo el producto/solución lo resuelve\n"
        creation_prompt += "- Usa formato comparativo o antes/después si es posible\n"
        creation_prompt += "- Incluye datos concretos (porcentajes, números) del impacto\n"
        creation_prompt += "- Formatos: Infografías, Caso de éxito, Antes / Después\n"
        creation_prompt += "- Ejemplos: '¿Por qué usar geotanques en vez de tambos?', 'Caso de éxito: Cómo [producto] resolvió [problema]', Comparativa visual antes/después\n\n"
    elif weekday_theme['day_name'] == 'Friday':
        creation_prompt += "📅 SEASONAL FOCUS FRIDAY:\n"
        creation_prompt += "- Enfócate en consejos o alertas basadas en temporadas regionales\n"
        creation_prompt += "- Considera el calendario de siembra, cosecha, poda, fertilización\n"
        creation_prompt += "- Incluye tips sobre clima estacional: qué esperar y cómo actuar\n"
        creation_prompt += "- Formatos: Infografías, Tutorial corto, Checklist operativo, Recordatorio de servicio, Seasonal weather tips\n"
        creation_prompt += "- Ejemplos: 'Este mes enfócate en mantenimiento del sistema de riego antes de temporada seca', 'Temporadas de cosecha: chiles, manzana', 'Alerta: Se esperan heladas esta semana'\n\n"
    elif weekday_theme['day_name'] == 'Saturday':
        creation_prompt += "👩‍🌾 PRODUCER SEGMENT FOCUS SATURDAY:\n"
        creation_prompt += f"- Enfócate en el sector: {weekday_theme['sector_rotation']}\n"
        creation_prompt += "- El contenido debe ser específico para ese tipo de productor\n"
        creation_prompt += "- Formatos: Infografías, FAQ/Mitos, Pro Tip, Interesting Fact, Tutorial corto, Recordatorio de servicio\n"
        creation_prompt += "- Ejemplos sectoriales:\n"
        if weekday_theme['sector_rotation'] == 'forestry':
            creation_prompt += "  - Forestal: 'Cómo almacenar agua para tus viveros forestales', 'Pro Tip: Mejores prácticas para viveros'\n"
        elif weekday_theme['sector_rotation'] == 'plant':
            creation_prompt += "  - Plantas: 'Riego eficiente con accesorios que sí duran', 'FAQ: ¿Cuándo es mejor momento para fertilizar?'\n"
        else:  # animal
            creation_prompt += "  - Ganadería: 'Evita fugas con abrazaderas resistentes para sistemas de agua para ganado', 'Interesting Fact: El agua representa X% del costo de producción'\n"
        creation_prompt += "\n"
    elif weekday_theme['day_name'] == 'Sunday':
        creation_prompt += "📊 INNOVATION / INDUSTRY REPORTS SUNDAY:\n"
        creation_prompt += "- Enfócate en noticias de la industria, innovación agrícola, estadísticas, o reportes\n"
        creation_prompt += "- Puede incluir novedades tecnológicas, normativas, tendencias, o trivia agrotech\n"
        creation_prompt += "- Mantén el tono informativo y actualizado\n"
        creation_prompt += "- Formatos: Industry novelty, Trivia agrotech-style post, Statistics or report highlights\n"
        creation_prompt += "- Ejemplos: 'Biofertilizantes: La nueva tendencia en agricultura', 'Trivia: ¿Sabías que México produce X% del aguacate mundial?', 'Reporte: Tendencias del campo en México 2025'\n\n"
    
    # Add universal content layer guidance
    creation_prompt += "✨ UNIVERSAL CONTENT LAYER (puedes usar en cualquier día):\n"
    creation_prompt += "- UGC (User Generated Content): Puede usarse cualquier día, en cualquier tipo de post\n"
    creation_prompt += "- Reels / Stories / TikTok: Adapta cualquier tipo de post a formato de video corto y atractivo\n"
    creation_prompt += "- Carousel Posts: Ideal para infografías, tutoriales, o formatos problema/solución\n"
    creation_prompt += "- Live / Polls / Q&A: Usa ocasionalmente para aumentar engagement\n\n"
    
    creation_prompt += f"{selected_product_info}\n"
    
    # Only include Durango context for Thursday, Friday, Saturday
    if needs_durango_context:
        creation_prompt += (
            f"CONTEXTO REGIONAL DURANGO (USA ESTA INFORMACIÓN PARA CONTENIDO RELEVANTE, PERO NO TE LIMITES SOLO A ESTO):\n"
            f"{durango_context[:800]}...\n\n"
            f"⚠️ NOTA SOBRE EL CONTEXTO: El contexto de Durango menciona actividades estacionales, pero NO debes limitarte solo a esos temas.\n"
            f"Puedes hablar de otros temas relevantes como planificación, optimización, educación, casos de éxito, etc.\n\n"
        )
    else:
        creation_prompt += (
            "⚠️ NOTA: El contexto regional de Durango NO está disponible para este día.\n"
            "Enfócate en contenido general que no requiera conocimiento regional específico.\n\n"
        )
    
    creation_prompt += (
        f"{CHANNEL_FORMATS}\n\n"
        
        "INSTRUCCIONES:\n"
        "1. Usa la información del producto si fue seleccionado, o crea contenido educativo general si no hay producto.\n"
        f"2. Canal: {strat_data.get('channel')} - Adapta el contenido a este canal específico.\n"
        "3. CAPTION POR CANAL: wa-status/stories (máx 50 chars), tiktok (máx 150), reels (máx 100), fb-post/ig-post (hasta 2000).\n"
        "   Para wa-status/stories/tiktok/reels: La imagen/video debe ser autoexplicativa, caption mínimo.\n"
        "4. REQUISITOS TÉCNICOS: Usa números exactos ('10-20 cm', '70% ahorro'), colores (Verde=bueno, Rojo=problema), tips en caja azul.\n"
        "5. Genera el contenido adaptado al canal y tipo de post.\n\n"
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
        
        "OUTPUT JSON:\n"
        "- TODOS los strings JSON deben estar entre comillas dobles y CERRADOS correctamente\n"
        "- Si un string contiene saltos de línea (\\n), escápalos como \\\\n\n"
        "- Si un string contiene comillas, escápalas como \\\"\n"
        "- NUNCA dejes strings sin cerrar - cada \" debe tener su \" de cierre\n"
        "- El JSON debe ser válido y parseable\n"
        "⚠️ REGLA CRÍTICA: Si NO es carrusel, DEBES proporcionar 'image_prompt' (NO null). Si ES carrusel, 'image_prompt' puede ser null.\n\n"
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
        f"REGLAS FINALES: Producto ID {selected_product_id or 'ninguno'}. Incluye logos IMPAG, personas cuando aplique, sé específico sobre el producto y su uso."
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

    # Normalize posting_time to HH:MM format (time only, not full datetime)
    posting_time_raw = data.get("posting_time", "")
    posting_time_normalized = None
    if posting_time_raw:
        try:
            # Try to parse various formats and extract time
            from datetime import datetime as dt_parse
            # If it's a full datetime string (ISO format)
            if 'T' in posting_time_raw or len(posting_time_raw) > 8:
                try:
                    parsed = dt_parse.fromisoformat(posting_time_raw.replace('Z', '+00:00'))
                    posting_time_normalized = parsed.strftime("%H:%M")
                except:
                    # Try other datetime formats
                    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]:
                        try:
                            parsed = dt_parse.strptime(posting_time_raw, fmt)
                            posting_time_normalized = parsed.strftime("%H:%M")
                            break
                        except:
                            continue
            # If it's already in HH:MM format
            elif len(posting_time_raw) == 5 and ':' in posting_time_raw:
                # Validate it's actually HH:MM
                parts = posting_time_raw.split(':')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    posting_time_normalized = posting_time_raw
        except Exception as e:
            social_logging.safe_log_warning(
                "[STEP 14] Failed to parse posting_time, using as-is",
                user_id=user_id,
                posting_time_raw=posting_time_raw,
                error=str(e)
            )
            posting_time_normalized = posting_time_raw[:5] if len(posting_time_raw) >= 5 else posting_time_raw
    
    # Update data with normalized posting_time
    if posting_time_normalized:
        data["posting_time"] = posting_time_normalized

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
    # Determine hookType: only "seasonality" on Fridays, otherwise "general"
    hook_type = "seasonality" if is_friday else "general"
    month_phase_actual = sales_context.get("phase", "general") if sales_context else "general"
    
    formatted_content = {
        "id": None,  # Will be set after save
        "postType": strat_data.get("post_type"),
        "channels": [strat_data.get("channel") or data.get("channel")],
        "hook": "Tendencias agrícolas",  # Default hook
        "hookType": hook_type,  # Only "seasonality" on Fridays, otherwise "general"
        "products": [product_details] if product_details else [],
        "tags": [],
        "instructions": notes_with_problem,
        "postingTime": data.get("posting_time"),  # Normalized to HH:MM format
        "generationSource": "llm",
        "strategyNotes": notes_with_problem,
        "carouselSlides": data.get("carousel_slides"),
        "needsMusic": data.get("needs_music", False),
        "generatedContext": {
            "monthPhase": month_phase_actual if is_friday else "general",  # Only include phase on Fridays
            "nearbyDates": [],
            "selectedCategories": [selected_category] if selected_category else []
        }
    }
    
    # Check if post already exists by topic_hash and date (avoid duplicates)
    social_logging.safe_log_info(
        "[STEP 15] Checking for existing post by topic_hash",
        user_id=user_id,
        topic_hash=topic_hash[:16] + "...",  # Log partial hash for debugging
        target_date=str(target_date)
    )
    existing_post = db.query(SocialPost).filter(
        SocialPost.topic_hash == topic_hash,
        SocialPost.date_for == target_date
    ).first()
    
    if existing_post:
        social_logging.safe_log_warning(
            "[STEP 15] Duplicate post detected - same topic_hash and date",
            user_id=user_id,
            existing_post_id=existing_post.id,
            existing_post_topic=existing_post.topic[:50] + "...",
            new_topic=normalized_topic[:50] + "..."
        )
    
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
        existing_post.content_tone = strat_data.get("content_tone")
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
            content_tone=strat_data.get("content_tone"),
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
        content_tone=strat_data.get("content_tone"),  # From strategy phase
        channel=strat_data.get("channel") or data.get("channel"),  # From strategy phase, fallback to content phase
        carousel_slides=data.get("carousel_slides"),
        needs_music=data.get("needs_music"),
        topic=canonical_topic,  # Canonical topic from strategy phase
        problem_identified=strat_data.get("problem_identified", ""),  # From strategy phase
        saved_post_id=saved_post_id  # Return the saved post ID
    )








