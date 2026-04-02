"""
Topic Engine: Identifies agricultural problems and topics.

This module handles STEP 1 of the multi-step pipeline:
- Input: date, recent topics, weekday theme
- Output: topic, problem, angle, urgency, audience
- Prompt size: ~800 tokens (vs 7,925 in old system)
"""
from pydantic import BaseModel
from typing import Optional
import anthropic
import json
import re
from social_config import DURANGO_SEASONALITY_CONTEXT, IMPAG_BRAND_CONTEXT


class TopicStrategy(BaseModel):
    """Output from Topic Engine."""
    topic: str  # "Error → Daño concreto → Solución" or short title
    problem_identified: str
    angle: str  # "riego", "fertilización", "plagas", etc.
    urgency_level: str  # "high", "medium", "low"
    target_audience: str  # "plant", "animal", "forestry", "general"


def _call_topic_llm_low_temp(client: anthropic.Anthropic, prompt: str) -> 'TopicStrategy':
    """Same as _call_topic_llm but with lower temperature for correction retries."""
    return _call_topic_llm(client, prompt, temperature=0.2)


def _call_topic_llm(client: anthropic.Anthropic, prompt: str, temperature: float = 0.7) -> 'TopicStrategy':
    """Call LLM with a prompt and parse the TopicStrategy JSON response."""
    try:
        import social_logging
        social_logging.safe_log_info("[TOPIC ENGINE] Prompt built", prompt_length=len(prompt), full_prompt=prompt)
    except Exception:
        pass

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}]
    )

    content = response.content[0].text.strip()

    try:
        import social_logging
        social_logging.safe_log_info("[TOPIC ENGINE] LLM response received", raw_response=content)
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
        raise ValueError(f"Failed to parse JSON from LLM response: {e}\nContent: {content}")

    topic_strategy = TopicStrategy(**data)

    try:
        import social_logging
        social_logging.safe_log_info(
            "[TOPIC ENGINE] Topic generated successfully",
            topic=topic_strategy.topic,
            angle=topic_strategy.angle,
            urgency=topic_strategy.urgency_level,
            audience=topic_strategy.target_audience
        )
    except Exception:
        pass

    return topic_strategy


def generate_topic_strategy(
    client: anthropic.Anthropic,
    date_str: str,
    weekday_theme: dict,
    recent_topics: list,
    seasonality_context: Optional[str] = None,  # Deprecated - kept for backward compatibility, not used
    user_suggested_topic: Optional[str] = None,
    is_second_post: bool = False,
    special_date: Optional[dict] = None
) -> TopicStrategy:
    """
    Generate topic strategy using LLM.

    Args:
        client: Anthropic client
        date_str: Date string (YYYY-MM-DD)
        weekday_theme: Weekday theme dict from config
        recent_topics: List of recent topic strings
        seasonality_context: DEPRECATED - not used; detailed Durango context is embedded for Friday posts
        user_suggested_topic: Optional user-suggested topic
        is_second_post: Whether this is the second post (e.g., Monday's "La Vida en el Rancho")

    Returns:
        TopicStrategy object with topic, problem, angle, etc.
    """
    # For social-type special dates, use a completely different prompt —
    # the normal "identify an agricultural problem" task is irrelevant here.
    if special_date and special_date.get('special_date_type') == 'social':
        special_date_name = special_date['special_date_name']
        prompt = f"""Genera el tema para un post de FELICITACIÓN por {special_date_name}.

FECHA: {date_str}
MARCA: IMPAG Agricultura Inteligente (insumos agrícolas, Durango, México)

INSTRUCCIONES:
- El post es una FELICITACIÓN SINCERA y EMOTIVA, como una tarjeta de celebración
- Tono: cálido, humano, cercano — celebra a las personas que protagonizan esta fecha
- Puedes hacer una referencia breve y natural al campo o a los productores, pero NO es obligatorio
- NO inventes estadísticas, científicos, ni datos específicos
- NO hagas un post de ventas, ingresos, estrategias de negocio ni biotecnología
- NO menciones productos de IMPAG
- El topic debe ser una frase de felicitación simple y genuina

RESPONDE SOLO CON JSON (sin markdown):
{{
  "topic": "Frase de felicitación cálida y genuina por {special_date_name}",
  "problem_identified": "Celebración de {special_date_name}",
  "angle": "celebración",
  "urgency_level": "high",
  "target_audience": "general"
}}
"""
        # Skip all the rest of the prompt-building logic
        return _call_topic_llm(client, prompt)

    # Build compact prompt (~800 tokens) for normal (non-social-date) days
    prompt = f"""Identifica un problema agrícola real para productores comerciales.

{IMPAG_BRAND_CONTEXT}
FECHA: {date_str}
DÍA DE LA SEMANA: {weekday_theme['day_name']}
TEMA DEL DÍA: {weekday_theme['theme']}

"""

    # Inject special date context for holiday/agricultural dates
    if special_date:
        special_date_type = special_date.get('special_date_type', 'agricultural')
        special_date_name = special_date['special_date_name']

        if special_date_type == 'holiday':
            prompt += f"""⚠️ EFEMÉRIDE DEL DÍA: HOY ES {special_date_name.upper()}
El post de hoy DEBE conmemorar esta fecha cívica/nacional.
- Tono: respetuoso, orgulloso, patriótico — con conexión al campo y la agricultura mexicana
- No es un post promocional, es de reconocimiento y celebración

"""
        else:  # agricultural
            prompt += f"""⚠️ EFEMÉRIDE DEL DÍA: HOY ES {special_date_name.upper()}
El post de hoy DEBE estar relacionado con esta fecha especial.
Conecta el tema con la agricultura, el campo y los productores de Durango.

"""

    # Add recent topics for variety
    if recent_topics:
        prompt += "TEMAS RECIENTES (ÚLTIMOS 14 DÍAS) - ELIGE ALGO DIFERENTE:\n"
        for topic in recent_topics[:10]:  # Max 10 recent
            prompt += f"- {topic}\n"
        prompt += """
⚠️ CRÍTICO: Tu tema DEBE ser COMPLETAMENTE DIFERENTE a los temas recientes arriba.

Ejemplos de cómo variar (SOLO EJEMPLOS - no te limites a estos):
- Si hay varios sobre "cosecha", elige algo como "almacenamiento" o "comercialización" o "maquinaria"
- Si hay varios sobre "suelo", elige algo como "tecnología" o "forestal" o "postcosecha"
- Puedes elegir CUALQUIER tema agrícola relevante: producción, procesamiento, comercialización,
  financiamiento, innovación, ganadería, forestal, tecnología, gestión, certificaciones,
  maquinaria, construcciones, energía, etc.

NO estás limitado a los ejemplos mencionados. Piensa en problemas reales que los productores
enfrentan en CUALQUIER área de su operación.

"""
    else:
        prompt += "No hay temas recientes - puedes elegir cualquier tema relevante.\n\n"

    # Add detailed Durango seasonality context for FRIDAY posts only (Seasonal Focus theme)
    day_name = weekday_theme['day_name']
    if day_name == 'Friday':
        if user_suggested_topic:
            # When user provides a specific topic, seasonality context is background only —
            # do NOT instruct the LLM to generate a seasonal/calendar topic from it.
            prompt += f"""CONTEXTO REGIONAL DURANGO (referencia de fondo):

{DURANGO_SEASONALITY_CONTEXT}

ℹ️ Usa este contexto solo como REFERENCIA REGIONAL si es relevante para el tema del usuario.
No generes un calendario de siembra ni un post estacional genérico — el tema ya está definido por el usuario.

"""
        else:
            prompt += f"""CONTEXTO ESTACIONAL DURANGO (CRÍTICO PARA VIERNES):

{DURANGO_SEASONALITY_CONTEXT}

⚠️ IMPORTANTE: Usa el contexto de Durango arriba para generar temas ESTACIONALES precisos.
- Considera los ciclos agrícolas correctos por mes (temporal Mayo-Junio, NO Febrero)
- Considera los cultivos principales: frijol (301,375 ha), maíz forrajero (2.3M t), alfalfa (2.5M t)
- 79% rainfed/temporal - esto es CRÍTICO para entender el calendario agrícola real
- Considera los problemas reales: 94.9% costos altos, 34% pérdida fertilidad suelo, financiamiento 8.5%
- Productos IMPAG relevantes: mallasombra (39.7% agro protegida), invernaderos (36.4%), riego, antiheladas

"""

    # Add task instructions - format varies by weekday
    day_name = weekday_theme['day_name']

    if day_name == 'Tuesday':
        # Tuesday = Promotion day — topic must connect to a physical product IMPAG can sell
        if user_suggested_topic:
            prompt += f"""🔴 TEMA OBLIGATORIO DEL USUARIO: "{user_suggested_topic}"
El post DEBE ser sobre este tema. NO lo ignores ni lo reemplaces.

TU TAREA:
Formula el tema del usuario en el formato "Error → Consecuencia → Solución con producto":
   - ERROR: La práctica incorrecta relacionada con "{user_suggested_topic}"
   - CONSECUENCIA: Daño concreto y descriptivo — NO inventes porcentajes ni cifras
   - SOLUCIÓN: Técnica específica que involucre un insumo o equipo físico (producto vendible)

⚠️ MARTES = DÍA PROMOCIONAL — REGLAS CRÍTICAS:
- La SOLUCIÓN debe involucrar un producto físico que IMPAG puede vender:
  equipos de aspersión, sistemas de riego, fertilizantes, sustratos, mallasombra,
  herramientas, plaguicidas, semillas, materiales de invernadero, bombas, etc.
- NO uses como solución: capacitaciones, certificaciones, talleres, asesorías, protocolos de gestión
- El tema debe poder conectarse a un producto del catálogo IMPAG
- DEBES usar EXACTAMENTE este formato: "Error → Consecuencia → Solución"
- NO inventes porcentajes ni cifras fabricadas

Ejemplos CORRECTOS para martes:
- "No calibrar la aspersora → Dosis desigual deja zonas sin proteger y desperdicia producto → Aspersor de mochila calibrado con boquilla regulable"
- "Regar sin control de caudal → Suelo compactado y raíces asfixiadas en temporal → Sistema de riego por goteo con regulador de presión"
- "Usar mallasombra inadecuada → Quema de plántulas y pérdida de stand → Mallasombra 35% calibrada para Durango"

RESPONDE SOLO CON JSON (sin markdown):
{{
  "topic": "Error específico → Consecuencia concreta → Solución con producto físico (sobre {user_suggested_topic})",
  "problem_identified": "Descripción del problema real relacionado con {user_suggested_topic}",
  "angle": "producto o insumo físico que resuelve el problema",
  "urgency_level": "high|medium|low",
  "target_audience": "plant|animal|forestry|general"
}}
"""
        else:
            prompt += """TU TAREA:
1. Identifica un problema agrícola REAL que productores enfrentan y que se resuelve con un producto físico
2. Formula como: "Error → Consecuencia → Solución con producto"
   - ERROR: Práctica incorrecta específica
   - CONSECUENCIA: Daño concreto y descriptivo — NO inventes porcentajes ni cifras
   - SOLUCIÓN: Técnica que involucre un insumo o equipo físico que IMPAG puede vender

⚠️ MARTES = DÍA PROMOCIONAL — REGLAS CRÍTICAS:
- La SOLUCIÓN debe ser un producto físico vendible: equipos de aspersión, sistemas de riego,
  fertilizantes, sustratos, mallasombra, herramientas, plaguicidas, semillas, bombas, etc.
- NO uses como solución: capacitaciones, certificaciones, talleres, asesorías, protocolos de gestión
- El tema DEBE poder conectarse a algo del catálogo IMPAG
- NO inventes porcentajes ni cifras fabricadas

Ejemplos CORRECTOS para martes:
- "No calibrar la aspersora → Dosis desigual deja zonas sin proteger y desperdicia producto → Aspersor con boquilla regulable y calibración correcta"
- "Regar sin control de caudal → Suelo compactado y raíces asfixiadas → Sistema de riego por goteo con regulador de presión"
- "Usar mallasombra inadecuada → Quema de plántulas y pérdida de stand en invernadero → Mallasombra 35% para clima semi-árido"
- "Fertilizar sin análisis de suelo → Exceso de sales daña raíces → Kit de análisis de suelo + fertilizante balanceado"
- "Almacenar agroquímicos sin equipo de protección → Intoxicación del trabajador y multas sanitarias → Traje de protección y kit de aspersión segura"

RESPONDE SOLO CON JSON (sin markdown):
{
  "topic": "Error específico → Consecuencia concreta y descriptiva → Solución con producto físico",
  "problem_identified": "Descripción del problema real que enfrenta el productor",
  "angle": "producto o insumo físico que resuelve el problema",
  "urgency_level": "high|medium|low",
  "target_audience": "plant|animal|forestry|general"
}
"""
    elif day_name == 'Thursday':
        # Thursday = Problem & Solution — educational, no product constraint
        if user_suggested_topic:
            prompt += f"""🔴 TEMA OBLIGATORIO DEL USUARIO: "{user_suggested_topic}"
El post DEBE ser sobre este tema. NO lo ignores ni lo reemplaces.

TU TAREA — PENSAMIENTO EN DOS PASOS:

PASO 1 — MAPEA EL PROCESO:
Antes de generar el tema, descompón mentalmente "{user_suggested_topic}" en sus fases.
Todo producto agrícola o agro-industrial tiene un proceso completo, por ejemplo:
  - Cultivo básico: preparación de suelo → siembra → riego/nutrición → control de plagas → cosecha
  - Valor agregado: cosecha → selección → transformación (tostado, pelado, secado, etc.) → almacenamiento → empaque → comercialización
  - Ganadería: alimentación → salud animal → ordeña/engorde → procesamiento → distribución

Identifica TODAS las fases que aplican a "{user_suggested_topic}".

PASO 2 — ENCUENTRA LA OPORTUNIDAD DE MEJORA:
En cada fase, pregúntate:
  - ¿Qué se hace manualmente que podría mecanizarse?
  - ¿Qué se hace de forma artesanal que podría estandarizarse?
  - ¿Qué cuello de botella limita el volumen o la calidad?
  - ¿Qué práctica obsoleta tiene una solución moderna disponible?
  - ¿Qué infraestructura falta para proteger el producto o el proceso?

Elige la fase con mayor oportunidad de impacto y formula el tema ahí.

FORMATO DE SALIDA:
   - ERROR: La práctica ineficiente, obsoleta o ausente en esa fase
   - CONSECUENCIA: El daño concreto — calidad, volumen, costo, tiempo — sin inventar cifras
   - SOLUCIÓN: La técnica, equipo, maquinaria o infraestructura que optimiza esa fase

⚠️ REGLAS:
- DEBES usar EXACTAMENTE este formato: "Error → Consecuencia → Solución"
- El tema DEBE estar relacionado con: "{user_suggested_topic}"
- La solución puede ser cualquier cosa que mejore el proceso: práctica agronómica, equipo de campo, maquinaria de procesamiento, infraestructura de almacenamiento, tecnología de comercialización
- NO inventes porcentajes ni cifras fabricadas
- NO uses preguntas como "¿Sabías que...?"

Ejemplos del razonamiento correcto:
- Tema "chile pasado" → fases: cultivo → cosecha → tostado → pelado → secado (El Sereno) → empaque
  → Oportunidad en TOSTADO: "Tostar chile a mano → Quemado desigual baja la calidad del producto final → Tostador rotativo con control de temperatura"
  → Oportunidad en SECADO: "Secar chile sin estructura adecuada → Lluvia o humedad nocturna arruina el lote en proceso → Secador solar con plástico de invernadero UV + estructura metálica"
  → Oportunidad en PELADO: "Pelar chile manualmente → Cuello de botella que limita el volumen procesado → Peladora mecánica de volteo semi-industrial"
- Tema "frijol" → fases: siembra → crecimiento → cosecha → secado → almacenamiento → comercialización
  → Oportunidad en ALMACENAMIENTO: "Almacenar frijol sin control de humedad → Hongos arruinan lotes en bodega → Silo metálico hermético con ventilación controlada"

⚠️ FORMATO OBLIGATORIO DEL CAMPO "topic":
- USA EXACTAMENTE: "Práctica incorrecta → Consecuencia → Solución"
- El separador ES "→" (flecha) — NO uses "=", ":", "-" ni ningún otro símbolo
- DEBE haber exactamente 2 flechas "→" (3 partes)
- Ejemplo válido: "Tostar chile a mano → Quemado desigual baja calidad → Tostador rotativo con temperatura controlada"
- Ejemplo INVÁLIDO: "Chile inconsistente = contratos perdidos = clasificar mejor" ❌

RESPONDE SOLO CON JSON (sin markdown):
{{
  "topic": "Práctica incorrecta → Consecuencia concreta → Solución técnica (usa → no =)",
  "problem_identified": "Descripción del problema real relacionado con {user_suggested_topic}",
  "angle": "tema principal del contenido",
  "urgency_level": "high|medium|low",
  "target_audience": "plant|animal|forestry|general"
}}
"""
        else:
            prompt += """TU TAREA:
1. Identifica un problema agrícola REAL que productores enfrentan HOY
2. Formula como: "Error → Consecuencia → Solución"
   - ERROR: Acción incorrecta específica
   - CONSECUENCIA: Daño concreto y descriptivo — NO inventes porcentajes ni cifras
   - SOLUCIÓN: Técnica específica y accionable

⚠️ FORMATO CRÍTICO:
- DEBES usar EXACTAMENTE este formato: "Error → Consecuencia → Solución"
- DEBES incluir los símbolos "→" para separar las tres partes
- NO uses preguntas como "¿Sabías que...?" o "¿Te has preguntado...?"
- NO uses títulos estilo clickbait
- NO inventes porcentajes ("30%", "hasta 40%") — describe el daño sin cifras fabricadas

Ejemplos CORRECTOS:
- "Almacenar grano sin secar → Hongos arruinan lotes completos en clima húmedo → Secar a 14% de humedad antes de almacenar"
- "No calibrar la aspersora → Aplicación desigual deja zonas sin proteger y desperdicia producto → Calibrar antes de cada ciclo"
- "Vender en temporada alta sin contrato previo → Precio spot es el más bajo del año → Contratar comprador antes de sembrar"

RESPONDE SOLO CON JSON (sin markdown):
{
  "topic": "Error específico → Consecuencia concreta y descriptiva → Solución técnica accionable",
  "problem_identified": "Descripción del problema real que enfrenta el productor",
  "angle": "tema principal del contenido",
  "urgency_level": "high|medium|low",
  "target_audience": "plant|animal|forestry|general"
}
"""
    else:
        # Other days — day theme determines TONE/LENS, not the subject.
        # When user provides a topic, that topic is the subject; the day only shapes how it's presented.

        if user_suggested_topic:
            # ── USER TOPIC PATH ───────────────────────────────────────────────
            # The user's topic drives generation. Day format becomes a lens, not an override.
            prompt += f"""🔴 TEMA OBLIGATORIO DEL USUARIO: "{user_suggested_topic}"

El tema es "{user_suggested_topic}" — no lo cambies, no lo reemplaces, no lo interpretes como otra cosa.
El día de la semana solo define el TONO con que presentas este tema.

TU TAREA:
Genera un título para un post sobre "{user_suggested_topic}" aplicando el ángulo de {day_name} ({weekday_theme['theme']}).

ÁNGULO DEL DÍA:
"""
            if day_name == 'Monday':
                if is_second_post and weekday_theme.get('theme') == '🌾 La Vida en el Rancho':
                    prompt += f"""Encuentra el lado EMOCIONAL y HUMANO de "{user_suggested_topic}".
- ¿Qué significa este tema para la vida, el sacrificio o el legado del productor?
- Elige un pilar: Fe, Sacrificio sin reconocimiento, Legado generacional, o Melancolía rural
- Tono: poético, rural, auténtico — no motivacional ni comercial
"""
                else:
                    prompt += f"""Encuentra el lado INSPIRADOR o MOTIVACIONAL de "{user_suggested_topic}".
- ¿Qué lección, perspectiva positiva o historia de éxito puede salir de este tema?
- Tono: motivador, esperanzador, orientado al logro del productor
"""
            elif day_name == 'Wednesday':
                prompt += f"""Encuentra el lado EDUCATIVO o PRÁCTICO de "{user_suggested_topic}".
- ¿Qué debe saber el productor sobre este tema? ¿Cuál es el proceso, la guía, los pasos?
- Tono: enseñanza clara, práctica, accionable
"""
            elif day_name == 'Friday':
                prompt += f"""Encuentra la relevancia ESTACIONAL o REGIONAL de "{user_suggested_topic}" para Durango.
- ¿Qué oportunidad económica, valor agregado o contexto estacional tiene este tema en la región?
- Conecta con el ciclo agrícola o la cultura local si es natural
- NO generes un calendario de siembra genérico — el foco es "{user_suggested_topic}"
"""
            elif day_name == 'Saturday':
                sector = weekday_theme.get('sector', 'general')
                prompt += f"""Presenta "{user_suggested_topic}" desde la perspectiva del sector {sector.upper()}.
- ¿Cómo aplica este tema a productores de {sector} en Durango?
- ¿Qué ángulo técnico o práctico es más relevante para este sector?
"""
            elif day_name == 'Sunday':
                prompt += f"""Encuentra el ángulo de INNOVACIÓN o TENDENCIA en "{user_suggested_topic}".
- ¿Qué hay de nuevo, moderno o emergente en este tema?
- ¿Cómo está evolucionando en la industria agrícola?
"""

            prompt += f"""
⚠️ REGLAS:
- NO uses "Error → Consecuencia → Solución" (ese es formato de Martes/Jueves)
- NO ignores ni reemplaces "{user_suggested_topic}" por otro tema
- El título debe nombrar explícitamente "{user_suggested_topic}" o referirse directamente a él

RESPONDE SOLO CON JSON (sin markdown):
{{
  "topic": "Título específico sobre {user_suggested_topic} con el ángulo de {day_name}",
  "problem_identified": "Descripción del contexto o valor de {user_suggested_topic} para el productor",
  "angle": "tema principal del contenido",
  "urgency_level": "high|medium|low",
  "target_audience": "plant|animal|forestry|general"
}}
"""

        else:
            # ── FREE GENERATION PATH ──────────────────────────────────────────
            # No user topic — day determines both WHAT and HOW.
            prompt += f"""TU TAREA:
Genera un tema apropiado para {day_name} ({weekday_theme['theme']}).

⚠️ FORMATO PARA {day_name.upper()}:
"""
            if day_name == 'Monday':
                # Check if this is the second post for Monday ("La Vida en el Rancho")
                if is_second_post and weekday_theme.get('theme') == '🌾 La Vida en el Rancho':
                    prompt += """- Este es un post de "La Vida en el Rancho" - literatura emocional rural
- NO es motivacional tradicional, NO es humor, NO es liderazgo
- Es poesía rural auténtica que conecta emocionalmente con la vida del rancho

🎯 CUATRO PILARES EMOCIONALES (elige UNO como base):

1️⃣ FE (Agricultura Espiritual)
   - Conceptos: fe, confiar, esperanza, sin garantías, propósito
   - La agricultura como acto de fe, no solo negocio
   - Ejemplo: "La fe del campesino empieza antes de la lluvia"

2️⃣ SACRIFICIO SIN RECONOCIMIENTO
   - Conceptos: trabajar sin aplausos, aunque nadie lo vea, sin garantías
   - La dignidad del trabajo invisible
   - Ejemplo: "trabajo que nadie ve pero que sostiene todo"

3️⃣ LEGADO GENERACIONAL
   - Conceptos: padre, enseñanza, hijos, herencia, ejemplo
   - Identidad familiar y continuidad
   - Ejemplo: "Antes veía a mi papá llegar del campo..."

4️⃣ MELANCOLÍA RURAL
   - Conceptos: mesas vacías, silencio, hijos que se fueron, despedidas
   - Dolor nostálgico mezclado con orgullo
   - Ejemplo: "la mesa sigue ahí... pero sobran sillas"

📝 ESTRUCTURA DE ESCRITURA (5 PASOS):
1. Escena rural concreta (algo visual)
2. Expande al significado emocional
3. Eleva el sacrificio
4. Universaliza ("solo quien vive del campo entiende")
5. Cierre suave (nunca estridente)

⚠️ FORMATO CRÍTICO:
- Líneas CORTAS
- Espacio para respirar
- Casi poético
- NO clickbait
- NO venta
- NO tecnología
- Solo verdad humana rural

✅ EJEMPLOS DE TEMAS CORRECTOS:
- "Padres que enseñaron sin palabras" (Legado)
- "La tristeza de ver el rancho vacío en fiestas" (Melancolía)
- "Vender ganado no siempre es negocio, a veces es despedida" (Sacrificio + Melancolía)
- "Sembrar hoy para que otros coman mañana" (Sacrificio + Legado)
- "El campo es la primera línea de batalla" (Fe + Sacrificio)
"""
                else:
                    # Standard Monday motivational post
                    prompt += """- Usa un título inspirador o motivacional (NO usar "Error → Daño → Solución")
- Enfoque: Motivación, inspiración, perspectiva positiva
- Ejemplos CORRECTOS:
  * "5 lecciones de productores exitosos que transformaron su operación"
  * "Por qué la persistencia vale más que la perfección en agricultura"
  * "Cómo convertir un mal año en aprendizaje valioso"
"""
            elif day_name == 'Wednesday':
                prompt += """- Usa un título educativo claro (NO usar "Error → Daño → Solución")
- Enfoque: Enseñanza, explicación, guía práctica
- Ejemplos CORRECTOS:
  * "Guía completa de fertilización nitrogenada por etapa fenológica"
  * "Cómo interpretar un análisis de suelo sin ser agrónomo"
  * "3 métodos de control biológico que realmente funcionan"
"""
            elif day_name == 'Friday':
                prompt += """- Usa un título estacional/calendario (NO usar "Error → Daño → Solución")
- Enfoque: Temporada actual, clima, fechas importantes
- Ejemplos CORRECTOS:
  * "Calendario de siembra para ciclo primavera-verano 2026"
  * "Preparativos esenciales para temporada de heladas"
  * "Qué plantar ahora para cosechar en 90 días"
"""
            elif day_name == 'Saturday':
                # Check if this is a sector-specific post (forestry, plant, or animal)
                sector = weekday_theme.get('sector', 'general')

                if sector == 'forestry':
                    prompt += """- Este es un post SECTOR-ESPECÍFICO para FORESTAL 🌲
- NO usar "Error → Daño → Solución" - usa título técnico-práctico
- Enfoque: Problemas reales que enfrentan productores forestales/viveros

🌲 CONTEXTO DURANGO FORESTAL (USA ESTOS DATOS):
- 4.0 millones de hectáreas con uso forestal (más que agrícola)
- Producción: ~4.17M m³ pino + 0.80M m³ encino anualmente
- Líder nacional en aserrado ($1,512M MX en producción bruta)
- Riesgo de incendios: Jan-Jun (crítico Abr-Jun)
- Bajo valor agregado (oportunidad en productos diversificados)

🎯 PROBLEMAS FORESTALES PRIORITARIOS (elige UNO):
1. Prevención y manejo de incendios forestales (temporada crítica)
2. Tasas de supervivencia en reforestación con especies nativas
3. Gestión de agua para viveros forestales
4. Control de plagas en sistemas pino/encino
5. Baja diversificación de productos (más allá de madera aserrada)
6. Interrupción estacional de suministro a aserraderos

✅ EJEMPLOS DE TEMAS CORRECTOS:
- "Prevención de incendios forestales: checklist operativo abril-junio"
- "Cómo mejorar supervivencia en reforestación con pino nativo"
- "Sistemas de riego para viveros forestales en zonas semi-áridas"
- "Control de descortezadores en pino: identificación temprana"
- "Más allá del aserrado: productos forestales de valor agregado"

⚠️ IMPORTANTE:
- Usa datos técnicos reales (especies, volúmenes, temporadas)
- Enfoque práctico y accionable para productores forestales
- Considera estacionalidad (incendios, corte, transporte)
"""
                elif sector == 'plant':
                    prompt += """- Este es un post SECTOR-ESPECÍFICO para PLANTAS/CULTIVOS 🌾
- NO usar "Error → Daño → Solución" - usa título técnico-práctico
- Enfoque: Problemas reales que enfrentan agricultores de cultivos

🌾 CONTEXTO DURANGO AGRÍCOLA (USA ESTOS DATOS):
- 79% superficie temporal/rainfed (746k ha) - CRÍTICO
- Cultivos principales: frijol (301k ha), maíz forrajero (2.3M t), alfalfa (2.5M t)
- Frijol: grandes hectáreas pero rendimientos bajos (problema estructural)
- Ciclo Primavera-Verano domina (depende de lluvia)
- Agricultura protegida: 389 ha (mallasombra 39.7%, invernaderos 36.4%)
- 94.9% productores reportan costos altos como problema #1

🎯 PROBLEMAS AGRÍCOLAS PRIORITARIOS (elige UNO):
1. Dependencia de temporal (79%) y riesgo de sequía
2. Bajos rendimientos en frijol a pesar de área grande
3. Escalamiento de agricultura protegida
4. Pérdida de fertilidad del suelo (34% de productores)
5. Costos altos de insumos (94.9% problema dominante)
6. Eficiencia de riego para forrajes (maíz, alfalfa)

✅ EJEMPLOS DE TEMAS CORRECTOS:
- "Frijol temporal: cómo mejorar rendimiento sin más hectáreas"
- "Mallasombra vs invernadero: ROI real en cultivos protegidos"
- "Calendario preciso de siembra primavera-verano para Durango"
- "Manejo de fertilidad en suelos temporaleros: prácticas de bajo costo"
- "Riego eficiente en alfalfa: reducir evaporación y maximizar cortes"

⚠️ IMPORTANTE:
- Enfatiza dependencia temporal (79%) - esto define todo
- Usa datos reales de cultivos regionales (frijol, maíz, alfalfa)
- Considera ciclo Primavera-Verano (lluvia domina calendario)
- Aborda costos altos - problema #1 reportado por productores
"""
                elif sector == 'animal':
                    prompt += """- Este es un post SECTOR-ESPECÍFICO para GANADERÍA/ANIMAL 🐄
- NO usar "Error → Daño → Solución" - usa título técnico-práctico
- Enfoque: Problemas reales que enfrentan ganaderos y productores lácteos

🐄 CONTEXTO DURANGO GANADERO (USA ESTOS DATOS):
- 1.58 millones de cabezas de ganado
- Producción láctea: ~5.6M litros/día (2.0 mil millones litros/año)
- 3er lugar nacional en leche (11.4% del total)
- Comarca Lagunera: 21.7% de producción nacional láctea
- Sistema forage-livestock: 91% tonelaje agrícola es forraje
- Infraestructura: Grupo Lala oficinas corporativas en Gómez Palacio

🎯 PROBLEMAS GANADEROS PRIORITARIOS (elige UNO):
1. Gestión de costos de alimentación (eficiencia en forrajes)
2. Estrés térmico y estacionalidad láctea (volumen en verano)
3. Sistemas de agua para ganado (eficiencia y prevención de fugas)
4. Oportunidades de integración vertical láctea
5. Manejo de estiércol y valorización de residuos
6. Salud animal preventiva y vacunación

✅ EJEMPLOS DE TEMAS CORRECTOS:
- "Optimización de forraje: maíz + alfalfa para máxima conversión láctea"
- "Manejo de estrés térmico en verano: mantener producción láctea"
- "Sistemas de agua para ganado: prevenir fugas y reducir costos"
- "Oportunidades en lácteos: más allá de vender leche cruda"
- "Biodigestores en ganadería: estiércol → energía y fertilizante"

⚠️ IMPORTANTE:
- Sistema forage-livestock es INTEGRADO (forrajes alimentan ganado)
- Estacionalidad láctea: verano tiene más volumen
- Comarca Lagunera es contexto regional crítico
- Enfoque en economía operativa (costos, eficiencia, conversión)
"""
                else:
                    # Fallback for general Saturday (should not happen with new config)
                    prompt += """- Usa un título específico del sector (NO usar "Error → Daño → Solución")
- Enfoque: Información relevante para el sector del día (forestry/plant/animal)
- Ejemplos CORRECTOS:
  * "Manejo de reforestación con especies nativas: supervivencia real"
  * "Rotación de potreros: cálculo de carga animal óptima"
  * "Variedades de maíz más resistentes a sequía en el Bajío"
"""
            elif day_name == 'Sunday':
                prompt += """- Usa un título informativo sobre innovación/industria (NO usar "Error → Daño → Solución")
- Enfoque: Novedades, tendencias, estadísticas, tecnología
- Ejemplos CORRECTOS:
  * "Drones agrícolas: cuándo sí valen la inversión en 2026"
  * "Tendencias de mercado: qué cultivos están subiendo de precio"
  * "Agricultura de precisión accesible para productores pequeños"
"""

            prompt += """
Ejemplos INCORRECTOS para estos días:
- "No usar fertilizante → Pierdes 40% de rendimiento → Programa de fertilización" ❌ (este es formato de Martes/Jueves)
- "❄️ ¿Sabías que...? Te explico cómo" ❌ (clickbait)
- "La importancia de..." ❌ (demasiado general)

RESPONDE SOLO CON JSON (sin markdown):
{
  "topic": "Título descriptivo claro y específico apropiado para el tema del día",
  "problem_identified": "Descripción del problema o contexto relevante",
  "angle": "tema principal del contenido",
  "urgency_level": "high|medium|low",
  "target_audience": "plant|animal|forestry|general"
}
"""

    topic_strategy = _call_topic_llm(client, prompt)

    # Validate topic format - only check "Error → Daño → Solución" format on Tuesday/Thursday
    day_name = weekday_theme['day_name']

    if day_name in ['Tuesday', 'Thursday']:
        # Tuesday/Thursday must use "Error → Daño → Solución" format.
        # If the LLM returned a plain headline, retry once with a strict correction prompt.
        needs_retry = ('→' not in topic_strategy.topic) or (not validate_topic_format(topic_strategy.topic))

        if needs_retry:
            try:
                import social_logging
                social_logging.safe_log_warning(
                    f"[TOPIC ENGINE] {day_name} topic missing '→' format — retrying with correction prompt",
                    bad_topic=topic_strategy.topic,
                    day=day_name
                )
            except Exception:
                pass

            # Clean the bad topic before sending — strip newlines, phone numbers, excess whitespace
            import re as _re
            bad_topic_clean = _re.sub(r'\n+', ' ', topic_strategy.topic)           # collapse newlines
            bad_topic_clean = _re.sub(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', '', bad_topic_clean)  # strip phone numbers
            bad_topic_clean = _re.sub(r'#\w+', '', bad_topic_clean)               # strip hashtags
            bad_topic_clean = bad_topic_clean.strip()[:200]                        # truncate

            tuesday_note = (
                "\n- La SOLUCIÓN debe ser un producto físico vendible (equipo, insumo, herramienta)."
                "\n- NO uses capacitaciones, certificaciones ni protocolos de gestión como solución."
                if day_name == 'Tuesday' else ""
            )

            correction_prompt = f"""El siguiente tema NO está en el formato correcto para un post de {day_name}:
"{bad_topic_clean}"

DEBES reformularlo EXACTAMENTE como: "Error → Consecuencia → Solución"
Reglas estrictas:
- Usa " → " (flecha con espacios) para separar las 3 partes
- ERROR: la práctica incorrecta del productor (acción concreta)
- CONSECUENCIA: el daño real que ocurre (sin inventar porcentajes)
- SOLUCIÓN: la técnica o producto que lo resuelve{tuesday_note}

Ejemplos del formato CORRECTO:
- "No calibrar la aspersora → Dosis desigual deja zonas sin proteger → Aspersor con boquilla regulable calibrada"
- "No documentar trazabilidad → Cargamentos rechazados en frontera → Sistema de registro desde siembra hasta empaque"
- "Transportar fruta sin protección → Golpes y manchas bajan precio de venta → Caja plástica ventilada con acolchado interno"

RESPONDE SOLO CON JSON (sin markdown):
{{
  "topic": "Error concreto → Consecuencia real → Solución accionable",
  "problem_identified": "descripción del problema en una oración",
  "angle": "producto o práctica principal que resuelve el problema",
  "urgency_level": "high",
  "target_audience": "general"
}}"""

            corrected = _call_topic_llm_low_temp(client, correction_prompt)

            # If retry also failed, build a minimal valid topic from what we know
            if '→' not in corrected.topic:
                try:
                    import social_logging
                    social_logging.safe_log_warning(
                        f"[TOPIC ENGINE] {day_name} correction retry also failed — using fallback construction",
                        corrected_topic=corrected.topic
                    )
                except Exception:
                    pass
                # Construct a minimal valid topic from problem_identified and angle
                problem = (corrected.problem_identified or topic_strategy.problem_identified or "práctica incorrecta").split('.')[0][:60]
                angle = (corrected.angle or topic_strategy.angle or "solución técnica")[:60]
                corrected.topic = f"{problem} → daño en cultivo o producto → {angle}"

            topic_strategy = corrected

            try:
                import social_logging
                social_logging.safe_log_info(
                    f"[TOPIC ENGINE] {day_name} topic corrected",
                    corrected_topic=topic_strategy.topic
                )
            except Exception:
                pass
    else:
        # Other days should NOT use "Error → Daño → Solución" format
        if '→' in topic_strategy.topic and topic_strategy.topic.count('→') == 2:
            try:
                import social_logging
                social_logging.safe_log_warning(
                    f"[TOPIC ENGINE] {day_name} topic should NOT use 'Error → Daño → Solución' format - use descriptive title instead",
                    topic=topic_strategy.topic,
                    day=day_name
                )
            except Exception:
                pass

    return topic_strategy


def validate_topic_format(topic: str) -> bool:
    """
    Validate that topic follows required format.

    Args:
        topic: Topic string

    Returns:
        True if valid format, False otherwise
    """
    # Check for "→" separators (viral format)
    if '→' in topic:
        parts = topic.split('→')
        if len(parts) == 3:
            # Valid: "Error → Daño → Solución"
            return all(part.strip() for part in parts)

    # Also allow short descriptive titles (for educational days)
    # If no "→", it should be a reasonable length title
    if len(topic) >= 10 and len(topic) <= 150:
        return True

    return False
