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
    weekday_theme: Optional[Dict[str, Any]] = None,
    special_date: Optional[Dict[str, Any]] = None
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
    is_social_celebration = special_date and special_date.get('special_date_type') == 'social'

    if is_social_celebration:
        special_date_name = special_date['special_date_name']
        prompt += f"""TU TAREA - POST DE CELEBRACIÓN: {special_date_name.upper()}

Genera una FELICITACIÓN CÁLIDA Y GENUINA. El copy debe sentirse como un mensaje humano de IMPAG, no un artículo.

🎯 ESTRUCTURA DEL CAPTION (sigue este orden):
1. RECONOCIMIENTO: 1-2 oraciones que celebren a las personas que protagonizan esta fecha, con mención a su rol en el campo
2. PRESENCIA: Menciona dónde las ves en el trabajo diario (viveros, parcelas, invernaderos, ranchos, empresas agrícolas — elige los que apliquen)
3. VOZ DE MARCA: "En IMPAG Agricultura Inteligente celebramos su trabajo, su liderazgo y su impacto en la agricultura."
4. CIERRE EMOTIVO: Una frase poderosa de cierre antes del saludo
5. SALUDO FINAL: Feliz {special_date_name}
6. DATOS DE CONTACTO: Web, WhatsApp, Ubicación (ya los tienes arriba)
7. HASHTAGS: 6-8 relevantes incluyendo #{special_date_name.replace(' ', '')} y #IMPAG

⚠️ REGLAS CRÍTICAS:
- NO inventes estadísticas ni porcentajes
- NO menciones productos de IMPAG ni hagas ventas
- Tono: cálido, orgulloso, cercano — como una empresa que conoce a su comunidad
- Caption debe ser COMPLETO (no solo el tema): incluye párrafos, saludo y hashtags

✅ EJEMPLO DE ESTRUCTURA (adáptalo, no lo copies):
"Hoy reconocemos a las mujeres que todos los días trabajan la tierra, producen alimentos y sostienen comunidades rurales en todo México. Su conocimiento, disciplina y visión forman parte esencial del presente y del futuro del campo.

En viveros, parcelas, invernaderos, ranchos y empresas agrícolas, las mujeres impulsan innovación, productividad y sostenibilidad.

En IMPAG Agricultura Inteligente celebramos su trabajo, su liderazgo y su impacto en la agricultura.

Porque detrás de muchos cultivos exitosos hay una mujer tomando decisiones, resolviendo problemas y haciendo que las cosas sucedan.

🌱 Feliz {special_date_name}

📍 Nuevo Ideal, Durango
🌐 todoparaelcampo.com.mx
📲 WhatsApp: 677-119-7737

#DiaInternacionalDeLaMujer #MujeresEnElCampo #AgriculturaMexicana #IMPAG"

RESPONDE SOLO CON JSON (sin markdown):
{{
  "caption": "caption completo de felicitación con párrafos, saludo, contacto y hashtags",
  "image_prompt": "PROMPT DETALLADO siguiendo las instrucciones arriba (OBLIGATORIO - nunca null)",
  "cta": "llamada a la acción celebratoria",
  "suggested_hashtags": ["#{special_date_name.replace(' ', '')}", "#IMPAG", "#AgriculturaMexicana"],
  "channel": "{content_strategy.channel}",
  "needs_music": {str(channel_format.get('needs_music', False)).lower()},
  "posting_time": "HH:MM (hora sugerida en formato 24h)",
  "notes": "notas opcionales"
}}
"""

    elif is_rancho_post:
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

IMPORTANTE - REGLAS DE CAPTION:"""

    # Check if this is a Saturday sector-specific post
    elif weekday_theme and weekday_theme.get('sector'):
        sector = weekday_theme.get('sector', 'general')
        emotional_angle = weekday_theme.get('emotional_angle', '')
        problem_focus = weekday_theme.get('problem_focus', [])
        technical_depth = weekday_theme.get('technical_depth', '')
        durango_context = weekday_theme.get('durango_context', '')

        prompt += f"""TU TAREA - POST SECTOR-ESPECÍFICO DE {sector.upper()} ({content_strategy.tone}):
Este es contenido TÉCNICO-PRÁCTICO para productores de {sector}.

🎯 ÁNGULO EMOCIONAL: {emotional_angle}

📊 CONTEXTO DURANGO - {sector.upper()}:
{durango_context}

🔧 PROBLEMAS PRIORITARIOS A ABORDAR:
"""
        for idx, problem in enumerate(problem_focus[:6], 1):
            prompt += f"{idx}. {problem}\n"

        prompt += f"""
📝 PROFUNDIDAD TÉCNICA: {technical_depth}

⚠️ FORMATO CRÍTICO PARA SÁBADO SECTOR-ESPECÍFICO:
- Caption DEBE ser técnico pero ACCESIBLE
- Incluye DATOS REGIONALES de Durango (usa el contexto arriba)
- Enfoque práctico: qué hacer, cómo hacerlo, cuándo
- Números concretos: hectáreas, volúmenes, porcentajes, costos
- Considera estacionalidad si es relevante
- NO vendas productos - esto es educativo

"""

        if sector == 'forestry':
            prompt += """🌲 CONTENIDO FORESTAL - GUÍA ESPECÍFICA:
- Habla de especies reales: pino, encino, especies nativas
- Menciona prácticas específicas: reforestación, prevención incendios, manejo
- Incluye temporadas: riesgo incendios (Ene-Jun, crítico Abr-Jun)
- Aborda economía: aserrado, productos valor agregado
- Tono: Largo plazo, paciencia, inversión generacional

Estructura sugerida:
1. PROBLEMA: [Identifica problema forestal específico]
2. CONTEXTO DURANGO: [Usa datos regionales de arriba]
3. SOLUCIÓN TÉCNICA: [Pasos prácticos y accionables]
4. NÚMEROS REALES: [Datos de supervivencia, volúmenes, costos]
5. CTA EDUCATIVO: "¿Tu vivero enfrenta este problema? Comparte tu experiencia"

✅ Ejemplo de estructura:
"🌲 Supervivencia en reforestación con pino nativo en Durango

El problema: Tasas de supervivencia < 60% en reforestación.
Durango produce ~4.17M m³ de pino anualmente, pero la regeneración no sigue el ritmo.

Factores críticos de supervivencia:
1. CALIDAD DE PLANTA: Altura mínima 25cm, raíz bien desarrollada
2. ÉPOCA DE PLANTACIÓN: Antes de temporada de lluvias (Mayo-Junio)
3. PREPARACIÓN DE SITIO: Limpieza de competencia, cepa 30x30x30 cm
4. AGUA INICIAL: Riego en primeras 2 semanas crítico

En viveros forestales de Durango, plantas con estas prácticas logran supervivencia >75% primer año.

Temporada crítica: Abril-Junio (incendios), plantar antes o después.

¿Qué técnicas te han funcionado mejor en reforestación? 💬

#Forestal #Reforestación #Durango #Viveros #PinoNativo"

"""
        elif sector == 'plant':
            prompt += """🌾 CONTENIDO AGRÍCOLA - GUÍA ESPECÍFICA:
- Habla de cultivos reales de Durango: frijol, maíz forrajero, alfalfa
- Menciona desafío temporal (79% rainfed) - esto es CRÍTICO
- Incluye calendarios: ciclo Primavera-Verano, ventanas de siembra
- Aborda economía: costos altos (94.9%), rendimientos, ROI
- Tono: Ansiedad estacional, precisión de timing, dependencia climática

Estructura sugerida:
1. PROBLEMA: [Identifica problema agrícola específico]
2. CONTEXTO DURANGO: [Usa datos regionales de arriba - 79% temporal]
3. SOLUCIÓN TÉCNICA: [Pasos con calendario y timing preciso]
4. NÚMEROS REALES: [Rendimientos, hectáreas, costos]
5. CTA EDUCATIVO: "¿Cómo manejas este problema en tu parcela?"

✅ Ejemplo de estructura:
"🌾 Mejorar rendimiento en frijol temporal sin más hectáreas

El problema: Durango tiene 301,375 ha de frijol pero rendimientos bajos.
Con 79% de superficie temporal (dependiente de lluvia), cada gota cuenta.

Estrategia para máximo rendimiento:

1. SEMILLA CERTIFICADA: +20-30% rendimiento vs. semilla guardada
   Costo: $800-1,200/ha vs pérdida de 200-300 kg/ha

2. VENTANA DE SIEMBRA CRÍTICA:
   - Temporal: inicio de lluvias regulares (típicamente finales de Junio)
   - Límite: 25 de Julio (después, riesgo de heladas tempranas)

3. DENSIDAD CORRECTA: 120,000-140,000 plantas/ha
   - Muy denso: competencia por agua (fatal en temporal)
   - Muy ralo: desperdicia potencial

4. FERTILIZACIÓN MÍNIMA: 40-20-00 (N-P-K)
   - 94.9% de productores reportan costos altos
   - Fertilizar solo si análisis de suelo lo justifica

Con estas prácticas, productores en temporal logran 1.2-1.5 t/ha vs 0.8 t/ha promedio.

El temporal no cambia, pero tus prácticas sí. 💧

#Frijol #AgriculturaTemporal #Durango #Rendimiento"

"""
        elif sector == 'animal':
            prompt += """🐄 CONTENIDO GANADERO - GUÍA ESPECÍFICA:
- Habla de realidad láctea/ganadera: hato, producción, forrajes
- Menciona sistema integrado: forrajes (91% tonelaje) alimentan ganado
- Incluye economía: costos de alimentación, conversión, leche
- Aborda Comarca Lagunera (contexto regional crítico)
- Tono: Operativo diario, economía de conversión, eficiencia

Estructura sugerida:
1. PROBLEMA: [Identifica problema ganadero específico]
2. CONTEXTO DURANGO: [Usa datos regionales - 5.6M litros/día, Comarca Lagunera]
3. SOLUCIÓN TÉCNICA: [Pasos con datos de conversión/eficiencia]
4. NÚMEROS REALES: [Litros, conversión, costos, ROI]
5. CTA EDUCATIVO: "¿Qué te funciona en tu operación?"

✅ Ejemplo de estructura:
"🐄 Optimización de forraje: conversión eficiente en producción láctea

El problema: Forrajes son 91% del tonelaje agrícola en Durango.
Con 5.6M litros/día de producción láctea, eficiencia de conversión = rentabilidad.

Estrategia de alimentación eficiente:

1. BALANCE MAÍZ FORRAJERO + ALFALFA:
   - Maíz: energía (2.3M toneladas producidas en Durango)
   - Alfalfa: proteína (2.5M toneladas, forraje premium)
   - Proporción óptima: 60% maíz / 40% alfalfa (ajustar según análisis)

2. CONVERSIÓN ESPERADA:
   - Buena conversión: 1.3-1.5 kg materia seca → 1 litro leche
   - Mala conversión: >1.8 kg materia seca → 1 litro leche
   - Costo diferencial: $2-3/litro en forraje

3. MANEJO ESTACIONAL (CRÍTICO):
   - Verano: mayor producción láctea (estacionalidad nacional)
   - Verano también: estrés térmico reduce consumo
   - Solución: sombra, agua fresca, ajustar horarios alimentación

4. COMARCA LAGUNERA CONTEXT:
   - 21.7% producción nacional láctea
   - Sistema integrado: forage → dairy → processing (Grupo Lala)

Con conversión eficiente, operaciones lácteas mejoran margen $1.50-2.50/litro.

En ganadería, los detalles operativos hacen la diferencia. 📊

#Ganadería #Lechería #Forrajes #Durango #ComarcaLagunera"

"""

        prompt += """
IMPORTANTE - REGLAS DE CAPTION SECTOR-ESPECÍFICO:"""

    else:
        prompt += """IMPORTANTE - REGLAS DE CAPTION:
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
