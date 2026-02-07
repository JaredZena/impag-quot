"""
Content Engine: Generates caption and image_prompt.

This module handles STEP 4 of the multi-step pipeline:
- Input: topic, strategy, product (if selected), channel format
- Output: caption, image_prompt, cta, hashtags
- Prompt size: ~1,500 tokens (vs ~20k in old system)
"""
from typing import Optional, Dict, Any
import anthropic
import json
import re
from social_config import CHANNEL_FORMATS, CONTENT_RULES, CONTACT_INFO
import social_image_prompt


def generate_content(
    client: anthropic.Anthropic,
    topic_strategy,  # TopicStrategy from Topic Engine
    content_strategy,  # ContentStrategy from Strategy Engine
    product_details: Optional[Dict[str, Any]] = None,
    weekday_theme: Optional[Dict[str, Any]] = None
) -> dict:
    """
    Generate content (caption, image_prompt) using LLM.

    Args:
        client: Anthropic client
        topic_strategy: TopicStrategy from Topic Engine
        content_strategy: ContentStrategy from Strategy Engine
        product_details: Optional product details dict
        weekday_theme: Optional weekday theme dict

    Returns:
        Dict with caption, image_prompt, cta, suggested_hashtags
    """
    # Detect structure type for image generation
    # Pass weekday to ensure Thursday uses problem-solution, other days use educational framing
    weekday = weekday_theme.get('day_name') if weekday_theme else None
    structure_type, structure_guide = social_image_prompt.detect_structure_type(
        topic=topic_strategy.topic,
        post_type=content_strategy.post_type,
        weekday=weekday
    )

    # Build compact prompt (~1,500 tokens)
    prompt = f"""Genera contenido para este post.

TEMA: {topic_strategy.topic}
PROBLEMA: {topic_strategy.problem_identified}

ESTRATEGIA:
- Tipo de post: {content_strategy.post_type}
- Tono: {content_strategy.tone}
- Canal: {content_strategy.channel}

"""

    # Add product info (brief, if selected)
    if product_details:
        prompt += f"""PRODUCTO SELECCIONADO:
- Nombre: {product_details.get('name', 'N/A')}
- Categoría: {product_details.get('category', 'N/A')}
"""
        # Add 2-3 key features if available
        features = product_details.get('features', [])
        if features and isinstance(features, list):
            prompt += f"- Características: {', '.join(str(f) for f in features[:3])}\n"
        prompt += "\n"

    # Add format constraints (from config)
    channel_format = CHANNEL_FORMATS.get(content_strategy.channel, {})
    prompt += f"""FORMATO PARA {content_strategy.channel}:
- Aspecto: {channel_format.get('aspect_ratio', 'N/A')}
- Caption máx: {channel_format.get('caption_max_chars', 'N/A')} caracteres
- Prioridad: {channel_format.get('priority', 'balanced')}
"""

    if channel_format.get('needs_music'):
        prompt += f"- Música: OBLIGATORIO ({channel_format.get('music_style', 'trending')})\n"

    if channel_format.get('notes'):
        prompt += f"- Nota: {channel_format['notes']}\n"

    prompt += "\n"

    # Add content rules (§8, brief)
    prompt += "REGLAS DE CONTENIDO (§8):\n"
    for i, rule in enumerate(CONTENT_RULES, 1):
        prompt += f"{i}. {rule}\n"
    prompt += "\n"

    # Add contact info for CTA
    prompt += f"""CONTACTO (para CTA):
- Web: {CONTACT_INFO['web']}
- WhatsApp: {CONTACT_INFO['whatsapp']}
- Ubicación: {CONTACT_INFO['location']}

"""

    # Build detailed image prompt instructions using social_image_prompt module
    strat_data = {
        "channel": content_strategy.channel,
        "topic": topic_strategy.topic,
        "post_type": content_strategy.post_type
    }

    image_instructions = social_image_prompt.build_image_prompt_instructions(
        strat_data=strat_data,
        structure_type=structure_type,
        structure_guide=structure_guide,
        contact_info=CONTACT_INFO,
        selected_product_id=product_details.get('name') if product_details else None,
        weekday_theme=weekday_theme
    )

    # Append image prompt instructions to the prompt
    prompt += image_instructions + "\n\n"

    # Task instructions
    # Check if this is a "La Vida en el Rancho" post
    is_rancho_post = weekday_theme and weekday_theme.get('theme') == '🌾 La Vida en el Rancho'

    if is_rancho_post:
        prompt += f"""TU TAREA - POST DE "LA VIDA EN EL RANCHO":
Este es un post de literatura emocional rural, NO es contenido motivacional tradicional.

🎯 ESTRUCTURA DE ESCRITURA (5 PASOS - SIGUE EXACTAMENTE):

1. ESCENA RURAL CONCRETA (algo visual)
   Ejemplo: "La mesa del rancho sigue ahí..."

2. EXPANDE AL SIGNIFICADO EMOCIONAL
   Ejemplo: "...pero las sillas sobran"

3. ELEVA EL SACRIFICIO
   Ejemplo: "No es que falte pan, faltan voces"

4. UNIVERSALIZA
   Ejemplo: "Solo quien vive del campo entiende ese peso"

5. CIERRE SUAVE (nunca estridente)
   Ejemplo: "Y eso, en el rancho, pesa más que cualquier hambre"

⚠️ FORMATO CRÍTICO:
- Líneas CORTAS (una idea por línea)
- Espacio para respirar entre párrafos
- Ritmo casi poético, cadencia lenta
- NO uses emojis exagerados
- NO vendas nada
- NO menciones tecnología o productos
- Solo verdad humana y auténtica del rancho

📝 TONO Y ESTILO:
- Nostálgico pero no deprimente
- Orgullo mezclado con melancolía
- Lenguaje simple pero profundo
- Como micro-sermones o poesía rural
- Estructura: Afirmación → Expansión → Peso moral → Cierre emocional

✅ EJEMPLOS DE ESTRUCTURA CORRECTA:

Ejemplo 1 (Legado):
"Antes veía a mi papá llegar del campo con las manos llenas de tierra y el corazón lleno de amor.
Cenaba en silencio y yo no entendía el cansancio ni las preocupaciones que cargaba.
Hoy soy yo quien llega con la ropa sucia, los pies rendidos y la mente llena de pendientes.
Ahora lo entiendo todo.
Y aunque el cansancio a veces me venza, sigo trabajando para ser para mis hijos el mismo ejemplo de esfuerzo, amor y constancia que él fue para mí."

Ejemplo 2 (Fe):
"La fe del campesino empieza antes de la lluvia.
Cuando la tierra aún está seca y aun así se siembra.
Es creer sin señales, trabajar sin garantías, y confiar en que el cielo cumplirá su parte."

Ejemplo 3 (Melancolía + Sacrificio):
"Vender ganado no siempre es negocio.
A veces es despedida.
Es soltar lo que cuidaste en sequía y en abundancia,
lo que vio tus madrugadas y aguantó contigo los años duros.
No todo se mide en dinero.
Hay ventas que dejan la mano vacía... y el corazón apretado."

🚨 LO QUE NUNCA DEBES HACER:
❌ "¿Sabías que...? Te explico cómo" (clickbait)
❌ Mencionar productos o tecnología IMPAG
❌ Llamadas a la acción comerciales
❌ Lenguaje corporativo o técnico
❌ Acortar las líneas artificialmente - mantén el ritmo poético
❌ Usar tono motivacional tradicional tipo "¡Tú puedes!"

✅ LO QUE SÍ DEBES HACER:
- Caption LARGO (400-800 palabras) - Facebook premia dwell time
- Imagen simple: foto auténtica del rancho (manos trabajando, campo al amanecer, herramientas viejas)
- Sin CTA comercial - el CTA es emocional ("solo quien vive del campo entiende")
- Hashtags simples: #ElCampo #VidaRural #Rancho #Agricultura #Productor

IMPORTANTE - REGLAS DE CAPTION:
- Caption debe respetar el límite de caracteres del canal
- Para canales visuales (wa-status, tiktok, reels, stories): caption CORTO, contenido en imagen
- Para canales de texto (fb-post, ig-post): caption DEBE ser LARGO y SUSTANCIAL
  * NO uses solo preguntas o teasers como "¿Sabías que...? Te explico cómo"
  * DEBES EXPLICAR el concepto completo en el caption
  * Incluye datos, pasos, o información educativa real
  * El caption debe entregar VALOR por sí solo, no solo prometer información
  * Estructura sugerida para fb-post/ig-post: Hook → Explicación → Pasos/Tips → CTA
- image_prompt debe seguir TODAS las instrucciones detalladas arriba (logos IMPAG, dimensiones, estructura, estilo)
- Para TikTok: CARRUSEL DE 2-3 IMÁGENES (NO video) - genera carousel_slides con prompts individuales
- Incluye 5-8 hashtags relevantes en suggested_hashtags

EJEMPLOS DE CAPTION (para fb-post/ig-post):
❌ INCORRECTO: "❄️ ¿Sabías que sin cadena de frío pierdes hasta 30% del valor de tus cultivos? Te explico cómo"
✅ CORRECTO: "❄️ La cadena de frío es crítica en postcosecha - sin ella, pierdes hasta 30% del valor de tus cultivos.

Aquí está el proceso completo:

1. COSECHA (0-2h): Mantén producto a sombra inmediatamente
2. PRE-ENFRIAMIENTO (2-4h): Baja temperatura a 2-4°C lo más rápido posible
3. ALMACENAMIENTO: Cámara fría constante, sin romper la cadena
4. TRANSPORTE: Vehículo refrigerado certificado

Cada hora sin refrigeración acelera deterioro y reduce precio de venta. Invertir en cadena de frío se paga solo en 2-3 cosechas.

📞 ¿Necesitas asesoría en refrigeración postcosecha? Contáctanos al 677-119-7737"

RESPONDE SOLO CON JSON (sin markdown):
{{
  "caption": "texto del caption adaptado al canal - LARGO y EDUCATIVO para fb-post/ig-post",
  "image_prompt": "PROMPT DETALLADO siguiendo las instrucciones arriba (OBLIGATORIO - nunca null)",
  "carousel_slides": ["Slide 1 prompt...", "Slide 2 prompt...", "Slide 3 prompt..."] (SOLO para TikTok carrusel),
  "cta": "llamada a la acción clara",
  "suggested_hashtags": ["#agricultura", "#riego", "..."],
  "channel": "{content_strategy.channel}",
  "needs_music": {str(channel_format.get('needs_music', False)).lower()},
  "posting_time": "HH:MM (hora sugerida en formato 24h)",
  "notes": "notas opcionales"
}}
"""

    # Log the prompt (for debugging)
    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Prompt built",
            prompt_length=len(prompt),
            prompt_tokens_estimate=len(prompt) // 4,
            full_prompt=prompt
        )
    except Exception:
        pass  # Logging failure shouldn't break generation

    # Call LLM (increased max_tokens for detailed image prompts)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3072,
        temperature=0.8,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse JSON from response
    content = response.content[0].text.strip()

    # Log raw LLM response
    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] LLM response received",
            response_length=len(content),
            raw_response=content[:500] + "..." if len(content) > 500 else content  # Truncate long responses
        )
    except Exception:
        pass

    # Remove markdown code blocks if present
    if content.startswith("```"):
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            content = match.group(1).strip()
        else:
            content = content.replace("```json", "").replace("```", "").strip()

    # Parse JSON
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON from LLM response: {e}\nContent: {content}")

    # Validate required fields
    if not data.get('caption'):
        raise ValueError("Missing required field: caption")
    if not data.get('image_prompt'):
        raise ValueError("Missing required field: image_prompt")

    # Log parsed result
    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Content generated successfully",
            caption_length=len(data.get('caption', '')),
            has_image_prompt=bool(data.get('image_prompt')),
            has_cta=bool(data.get('cta')),
            hashtag_count=len(data.get('suggested_hashtags', [])),
            channel=data.get('channel')
        )
    except Exception:
        pass

    return data
