"""
Content Engine: Generates caption and image_prompt in two sequential LLM calls.

Step 4a: Caption generation — focused on message, tone, and channel format.
Step 4b: Image prompt generation — reads the final caption to ensure visual alignment.

Separating these two calls means the image always reflects the actual angle
the caption took, not just the raw topic keywords.
"""
from typing import Optional, Dict, Any
import anthropic
import json
import re
from social_config import CHANNEL_FORMATS, CONTENT_RULES, CONTACT_INFO, IMPAG_BRAND_CONTEXT, FEW_SHOT_USER_TOPIC_EXAMPLES
import social_image_prompt


# ── STEP 4a: CAPTION ─────────────────────────────────────────────────────────

def _get_day_example(weekday_theme: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the few-shot caption example for the current day, or None."""
    if not weekday_theme:
        return None
    day = weekday_theme.get('day_name', '')
    sector = weekday_theme.get('sector', '')

    # Saturday has three sector-specific examples
    if day == 'Saturday' and sector:
        key = f'Saturday_{sector}'
        ex = FEW_SHOT_USER_TOPIC_EXAMPLES.get(key)
    else:
        ex = FEW_SHOT_USER_TOPIC_EXAMPLES.get(day)

    return ex['caption'] if ex else None


def _build_caption_prompt(
    topic_strategy,
    content_strategy,
    product_details: Optional[Dict[str, Any]] = None,
    weekday_theme: Optional[Dict[str, Any]] = None,
    special_date: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the caption-only prompt."""
    channel_format = CHANNEL_FORMATS.get(content_strategy.channel, {})

    # Inject day-matched few-shot example when available
    example = _get_day_example(weekday_theme)
    example_block = ""
    if example:
        example_block = f"""EJEMPLO DE REFERENCIA (estilo, profundidad y formato esperados — NO copies el contenido, adapta el estilo al tema actual):
---
{example}
---

"""

    prompt = f"""Genera el caption para este post.

{IMPAG_BRAND_CONTEXT}
{example_block}TEMA: {topic_strategy.topic}
PROBLEMA: {topic_strategy.problem_identified}

ESTRATEGIA:
- Tipo de post: {content_strategy.post_type}
- Tono: {content_strategy.tone}
- Canal: {content_strategy.channel}

"""

    if product_details:
        prompt += f"""PRODUCTO DE APOYO (apoya el tema — NO es el protagonista del caption):
⚠️ El caption debe hablar del TEMA: "{topic_strategy.topic}"
⚠️ El producto aparece como la solución o herramienta — no como el sujeto principal.
- Nombre: {product_details.get('name', 'N/A')}
- Categoría: {product_details.get('category', 'N/A')}
"""
        features = product_details.get('features', [])
        if features and isinstance(features, list):
            prompt += f"- Características: {', '.join(str(f) for f in features[:3])}\n"
        prompt += "\n"

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

    prompt += "REGLAS DE CONTENIDO (§8):\n"
    for i, rule in enumerate(CONTENT_RULES, 1):
        prompt += f"{i}. {rule}\n"
    prompt += "\n"

    prompt += f"""CONTACTO (para CTA):
- Web: {CONTACT_INFO['web']}
- WhatsApp: {CONTACT_INFO['whatsapp']}
- Ubicación: {CONTACT_INFO['location']}

"""

    # Shared caption-only JSON schema (no image_prompt here)
    caption_json = (
        "RESPONDE SOLO CON JSON (sin markdown):\n"
        "{\n"
        '  "caption": "texto del caption completo adaptado al canal",\n'
        '  "cta": "llamada a la acción",\n'
        '  "suggested_hashtags": ["#hashtag1", "#hashtag2"],\n'
        f'  "channel": "{content_strategy.channel}",\n'
        f'  "needs_music": {str(channel_format.get("needs_music", False)).lower()},\n'
        '  "posting_time": "HH:MM",\n'
        '  "notes": "notas opcionales"\n'
        "}\n"
    )

    is_rancho_post = weekday_theme and weekday_theme.get('theme') == '🌾 La Vida en el Rancho'
    is_monday_motivational = (
        weekday_theme and
        weekday_theme.get('day_name') == 'Monday' and
        weekday_theme.get('theme') == '✊ Motivational / Inspirational' and
        topic_strategy.topic  # only when a real topic was provided
    )
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

{caption_json}"""

    elif is_monday_motivational:
        prompt += f"""TU TAREA - POST MOTIVACIONAL DE LUNES:
El tema es "{topic_strategy.topic}". Escribe una historia o reflexión HUMANA sobre este tema — no un artículo de tips.

🎯 ESTRUCTURA (sigue este orden):
1. ESCENA CONCRETA: Abre con un momento visual específico relacionado al tema (no una pregunta retórica)
2. EL PESO REAL: Expande al significado humano detrás de esa escena — qué carga, qué sacrifica, qué representa
3. EL RECONOCIMIENTO: Nombralo — "pocos lo ven", "nadie aplaude", "el campo lo sabe"
4. UNIVERSALIZA: Una línea que conecta con todo productor ("Solo quien vive del campo entiende")
5. CIERRE SUAVE: Una frase de cierre que deje algo, no que empuje a comprar

⚠️ REGLAS CRÍTICAS:
- Caption LARGO (300-600 palabras) — fb-post premia dwell time
- NO uses estructura "5 lecciones de..." ni "Te explico cómo..."
- NO menciones productos IMPAG ni hagas ventas
- NO uses lenguaje corporativo o motivacional tipo "¡Tú puedes!"
- IMPAG aparece solo al final, con un cierre de marca suave (1-2 líneas máximo)
- Hashtags: simples y rurales — #ElCampo #Durango #VidaRural #Rancho

✅ TONO CORRECTO: narrativo, cálido, con peso emocional real — como alguien que lo vivió y lo cuenta
❌ TONO INCORRECTO: publicitario, motivacional hueco, listicle de consejos

{caption_json}"""

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
- Sin CTA comercial - el CTA es emocional ("solo quien vive del campo entiende")
- Hashtags simples: #ElCampo #VidaRural #Rancho #Agricultura #Productor

IMPORTANTE - REGLAS DE CAPTION:
{caption_json}"""

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
- Incluye DATOS REGIONALES de Durango del contexto proporcionado arriba (hectáreas, volúmenes, estadísticas reales)
- Enfoque práctico: qué hacer, cómo hacerlo, cuándo
- NUNCA inventes porcentajes ni cifras que no estén en el contexto — si no tienes el número real, descríbelo cualitativamente
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
4. DATOS CONTEXTUALES: [Solo datos que aparecen en el contexto de Durango arriba — no inventes cifras]
5. CTA EDUCATIVO: "¿Tu vivero enfrenta este problema? Comparte tu experiencia"

✅ Ejemplo de estructura:
"🌲 Reforestación con pino nativo en Durango: los factores que más afectan la supervivencia

Durango produce ~4.17M m³ de pino anualmente y lidera la producción forestal nacional.
Pero la regeneración natural no sigue el ritmo de la extracción — la reforestación bien ejecutada es clave.

Factores críticos que determinan si la planta vive o muere:
1. CALIDAD DE PLANTA: Altura mínima de 25 cm, raíz bien desarrollada y sin deformaciones
2. ÉPOCA DE PLANTACIÓN: Plantar antes del inicio de lluvias (Mayo-Junio) para aprovechar la humedad
3. PREPARACIÓN DE SITIO: Limpieza de competencia, cepa de 30×30×30 cm mínimo
4. AGUA INICIAL: Las primeras dos semanas son críticas — sin humedad en ese periodo, la planta no prende

Temporada de alto riesgo: Abril-Junio (incendios forestales) — plantar antes o esperar después.

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
4. DATOS CONTEXTUALES: [Solo datos del contexto de Durango arriba — no inventes cifras ni rendimientos]
5. CTA EDUCATIVO: "¿Cómo manejas este problema en tu parcela?"

✅ Ejemplo de estructura:
"🌾 Mejorar rendimiento en frijol temporal sin más hectáreas

El problema: Durango tiene 301,375 ha de frijol pero rendimientos bajos.
Con 79% de superficie temporal (dependiente de lluvia), cada gota cuenta.

Estrategia para máximo rendimiento:

1. SEMILLA CERTIFICADA: Mejor germinación y uniformidad que semilla guardada de ciclos anteriores

2. VENTANA DE SIEMBRA CRÍTICA:
   - Temporal: inicio de lluvias regulares (típicamente finales de Junio)
   - Límite: 25 de Julio (después, riesgo de heladas tempranas)

3. DENSIDAD ADECUADA AL TEMPORAL:
   - Muy denso: plantas compiten por agua — fatal en año seco
   - Muy ralo: no aprovechas el potencial de la parcela
   - Consulta a tu técnico según variedad y ciclo esperado

4. FERTILIZACIÓN BASADA EN ANÁLISIS DE SUELO:
   - 94.9% de productores reportan costos altos como problema #1
   - Fertilizar sin análisis = gastar sin saber qué falta

El temporal no cambia, pero tus decisiones de manejo sí. 💧

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
4. DATOS CONTEXTUALES: [Solo datos del contexto de Durango arriba — no inventes cifras de conversión ni costos]
5. CTA EDUCATIVO: "¿Qué te funciona en tu operación?"

✅ Ejemplo de estructura:
"🐄 Eficiencia de forraje: la variable que más impacta tu costo por litro

El problema: Los forrajes representan 91% del tonelaje agrícola en Durango.
Con 5.6M litros/día de producción láctea en la región, la eficiencia de conversión define la rentabilidad.

Claves de una ración eficiente:

1. BALANCE MAÍZ + ALFALFA:
   - Maíz forrajero: fuente de energía (Durango produce 2.3M toneladas al año)
   - Alfalfa: fuente de proteína (2.5M toneladas, forraje premium regional)
   - Proporción ideal: depende del análisis de tu hato — consulta a tu nutriólogo

2. SEÑALES DE MALA CONVERSIÓN:
   - Animal consume pero no produce al nivel esperado
   - Heces con fibra visible sin digerir
   - Pérdida de condición corporal en alta producción

3. MANEJO DE ESTRÉS TÉRMICO (VERANO):
   - Calor reduce consumo voluntario de alimento
   - Estrategia: sombra, agua fresca ad libitum, ajustar horarios de alimentación

4. COMARCA LAGUNERA — CONTEXTO REGIONAL:
   - 21.7% de la producción láctea nacional
   - Sistema integrado forraje → lechería → procesamiento (Grupo Lala)

En ganadería, los detalles operativos hacen la diferencia. 📊

#Ganadería #Lechería #Forrajes #Durango #ComarcaLagunera"

"""

        prompt += f"""
IMPORTANTE - REGLAS DE CAPTION SECTOR-ESPECÍFICO:
{caption_json}"""

    else:
        prompt += f"""IMPORTANTE - REGLAS DE CAPTION:
- Caption debe respetar el límite de caracteres del canal
- Para canales visuales (tiktok, reels, stories): caption CORTO, contenido en imagen
- Para canales de texto (fb-post, ig-post): caption DEBE ser LARGO y SUSTANCIAL
  * NO uses solo preguntas o teasers como "¿Sabías que...? Te explico cómo"
  * DEBES EXPLICAR el concepto completo en el caption
  * Incluye datos, pasos, o información educativa real
  * El caption debe entregar VALOR por sí solo, no solo prometer información
  * Estructura sugerida para fb-post/ig-post: Hook → Explicación → Pasos/Tips → CTA

EJEMPLOS DE CAPTION (para fb-post/ig-post):
❌ INCORRECTO: "¿Sabías que sin cadena de frío pierdes hasta 30% del valor de tus cultivos? Te explico cómo"
  (Problema: es un teaser sin información, y el porcentaje "30%" es inventado)

✅ CORRECTO: "❄️ La cadena de frío en postcosecha es uno de los puntos más críticos — y más descuidados — en la comercialización agrícola.

Sin control de temperatura, el deterioro se acelera desde el momento de la cosecha. El producto llega al mercado en peores condiciones, baja el precio de venta y aumentan los rechazos.

El proceso correcto:
1. COSECHA (0-2h): Saca el producto del sol inmediatamente, lleva a zona sombreada
2. PRE-ENFRIAMIENTO (2-4h): Baja la temperatura del producto lo antes posible
3. ALMACENAMIENTO: Cámara fría constante — nunca interrumpas la cadena
4. TRANSPORTE: Vehículo refrigerado hasta el punto de entrega

Cada hora de retraso en el enfriamiento acorta la vida útil del producto y reduce tu margen de venta.

📞 ¿Dudas sobre manejo postcosecha? Escríbenos al 677-119-7737"
  (Correcto: explica el proceso completo, sin porcentajes inventados)

{caption_json}"""

    return prompt


def _generate_caption(
    client: anthropic.Anthropic,
    topic_strategy,
    content_strategy,
    product_details: Optional[Dict[str, Any]] = None,
    weekday_theme: Optional[Dict[str, Any]] = None,
    special_date: Optional[Dict[str, Any]] = None,
) -> dict:
    """Step 4a: Generate caption only."""
    prompt = _build_caption_prompt(
        topic_strategy, content_strategy, product_details, weekday_theme, special_date
    )

    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Caption prompt built",
            prompt_length=len(prompt),
            prompt_tokens_estimate=len(prompt) // 4,
            full_prompt=prompt
        )
    except Exception:
        pass

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        temperature=0.8,
        messages=[{"role": "user", "content": prompt}]
    )

    content = response.content[0].text.strip()

    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Caption LLM response received",
            response_length=len(content),
            raw_response=content[:500] + "..." if len(content) > 500 else content
        )
    except Exception:
        pass

    if content.startswith("```"):
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            content = match.group(1).strip()
        else:
            content = content.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse caption JSON: {e}\nContent: {content}")

    if not data.get('caption'):
        raise ValueError("Missing required field: caption")

    return data


# ── STEP 4b: IMAGE PROMPT ────────────────────────────────────────────────────

def _generate_image_prompt(
    client: anthropic.Anthropic,
    caption: str,
    topic_strategy,
    content_strategy,
    product_details: Optional[Dict[str, Any]] = None,
    weekday_theme: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Step 4b: Generate image_prompt using the actual caption as the primary reference.

    Structure detection runs against caption text (not just the raw topic),
    so the visual layout matches what the caption actually says.
    """
    weekday = weekday_theme.get('day_name') if weekday_theme else None

    # Use caption content to improve structure detection accuracy
    combined_text = f"{topic_strategy.topic} {caption[:300]}"
    structure_type, structure_guide = social_image_prompt.detect_structure_type(
        topic=combined_text,
        post_type=content_strategy.post_type,
        weekday=weekday
    )

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

    channel = content_strategy.channel
    is_tiktok = channel == "tiktok"
    is_carousel_channel = channel in ("tiktok", "fb-post", "ig-post")

    # Build carousel-specific override for TikTok
    if is_tiktok:
        carousel_override = """
⚠️⚠️⚠️ CANAL TIKTOK — CARRUSEL OBLIGATORIO ⚠️⚠️⚠️
Este post es un CARRUSEL de 2-3 imágenes individuales. NO generes una sola imagen multi-panel.

REGLAS ABSOLUTAS:
- Cada slide es una imagen COMPLETA e INDEPENDIENTE (no un panel dentro de otra imagen)
- Cada slide: vertical 1080×1920px, texto grande y legible (mín 60px), un solo concepto
- image_prompt = portada (Slide 1), carousel_slides = array con TODOS los slides (incluyendo la portada como primer elemento)
- 2 slides si el tema es simple, 3 slides si el tema tiene más pasos

ESTRUCTURA OBLIGATORIA para un tema de 3 consejos:
  Slide 1 (portada): Pregunta o gancho + elemento visual fuerte — hace que el usuario quiera deslizar
  Slide 2: Consejo 1 + ilustración — explica el primer punto
  Slide 3: Consejo 2-3 + CTA de contacto IMPAG

EJEMPLO de output correcto para "Riego eficiente":
{
  "image_prompt": "Slide 1/3 — PORTADA TikTok: Bold question '¿Estás tirando agua al regar?' Large white text on deep green IMPAG background, water drop icon, field background blurred. Vertical 1080x1920px. Logo IMPAG top right.",
  "carousel_slides": [
    "Slide 1/3 — PORTADA: '¿Estás tirando agua al regar?' Bold white text on IMPAG green, water drop icon, blurred field background. Vertical 1080x1920px. Logo IMPAG top right.",
    "Slide 2/3 — CONSEJO 1: 'Riega en la madrugada, no al mediodía'. Split showing wilted plant (noon sun) vs healthy plant (dawn irrigation). Large number '1' IMPAG green. Vertical 1080x1920px.",
    "Slide 3/3 — CONSEJO 2 + CTA: 'Sensor de humedad = no más suposiciones'. Close-up soil sensor in field. '¿Dudas? Escríbenos 677-119-7737'. Vertical 1080x1920px. Footer IMPAG."
  ]
}

⚠️ NUNCA pongas "4 paneles" o "multi-panel" en un solo image_prompt para TikTok.
   Cada punto = su propio slide independiente.
"""
    else:
        carousel_override = ""

    carousel_json = (
        '  "carousel_slides": ["Slide 1 prompt completo...", "Slide 2 prompt completo...", "Slide 3 prompt completo (opcional)"]'
        if is_tiktok
        else ('  "carousel_slides": ["Slide 1...", "Slide 2...", "...hasta 10 slides"]'
              if is_carousel_channel
              else '  "carousel_slides": null')
    )

    prompt = f"""Genera el image_prompt para este post de redes sociales.

CAPTION FINAL (ya generado — úsalo como referencia principal para la imagen):
---
{caption}
---

TEMA: {topic_strategy.topic}
TIPO DE POST: {content_strategy.post_type}
CANAL: {channel}
{carousel_override}
{image_instructions}

TAREA ESPECÍFICA:
Genera un image_prompt que represente visualmente el contenido REAL del caption anterior.
- Refleja el ángulo exacto que tomó el caption, no solo el tema general
- Si el caption explica un proceso paso a paso → la imagen muestra ese proceso
- Si el caption hace una comparación → la imagen refleja esa comparación
- Si el caption cuenta una historia emocional → la imagen transmite esa emoción
- Sigue el estilo visual (🎨) indicado en las instrucciones de arriba

RESPONDE SOLO CON JSON (sin markdown):
{{
  "image_prompt": "PROMPT DETALLADO OBLIGATORIO — describe estilo visual, composición, elementos, colores, dimensiones, branding IMPAG",
{carousel_json}
}}
"""

    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Image prompt generation started",
            structure_type=structure_type,
            caption_length=len(caption)
        )
    except Exception:
        pass

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}]
    )

    content = response.content[0].text.strip()

    if content.startswith("```"):
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            content = match.group(1).strip()
        else:
            content = content.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse image_prompt JSON: {e}\nContent: {content}")

    if not data.get('image_prompt'):
        raise ValueError("Missing required field: image_prompt")

    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Image prompt generated",
            has_carousel=bool(data.get('carousel_slides')),
            image_prompt_length=len(data.get('image_prompt', ''))
        )
    except Exception:
        pass

    return data


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def generate_content(
    client: anthropic.Anthropic,
    topic_strategy,
    content_strategy,
    product_details: Optional[Dict[str, Any]] = None,
    weekday_theme: Optional[Dict[str, Any]] = None,
    special_date: Optional[Dict[str, Any]] = None
) -> dict:
    """
    Generate caption + image_prompt using two sequential LLM calls.

    Step 4a generates the caption, step 4b reads that caption to produce
    an image_prompt that reflects what the caption actually says.
    """
    # Step 4a: caption
    caption_data = _generate_caption(
        client, topic_strategy, content_strategy, product_details, weekday_theme, special_date
    )

    # Step 4b: image_prompt informed by the actual caption
    image_data = _generate_image_prompt(
        client,
        caption=caption_data['caption'],
        topic_strategy=topic_strategy,
        content_strategy=content_strategy,
        product_details=product_details,
        weekday_theme=weekday_theme,
    )

    result = {**caption_data, **image_data}

    try:
        import social_logging
        social_logging.safe_log_info(
            "[CONTENT ENGINE] Content generated successfully",
            caption_length=len(result.get('caption', '')),
            has_image_prompt=bool(result.get('image_prompt')),
            has_carousel=bool(result.get('carousel_slides')),
            hashtag_count=len(result.get('suggested_hashtags', [])),
            channel=result.get('channel')
        )
    except Exception:
        pass

    return result
