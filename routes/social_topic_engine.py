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
from social_config import DURANGO_SEASONALITY_CONTEXT


class TopicStrategy(BaseModel):
    """Output from Topic Engine."""
    topic: str  # "Error → Daño concreto → Solución" or short title
    problem_identified: str
    angle: str  # "riego", "fertilización", "plagas", etc.
    urgency_level: str  # "high", "medium", "low"
    target_audience: str  # "plant", "animal", "forestry", "general"


def _call_topic_llm(client: anthropic.Anthropic, prompt: str) -> 'TopicStrategy':
    """Call LLM with a prompt and parse the TopicStrategy JSON response."""
    try:
        import social_logging
        social_logging.safe_log_info("[TOPIC ENGINE] Prompt built", prompt_length=len(prompt), full_prompt=prompt)
    except Exception:
        pass

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=0.7,
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

    if day_name in ['Tuesday', 'Thursday']:
        # Tuesday (Promotion) & Thursday (Problem & Solution) - use "Error → Daño → Solución" format
        if user_suggested_topic:
            prompt += f"""🔴 TEMA OBLIGATORIO DEL USUARIO: "{user_suggested_topic}"
El post DEBE ser sobre este tema. NO lo ignores ni lo reemplaces.

TU TAREA:
Formula el tema del usuario en el formato "Error → Consecuencia → Solución":
   - ERROR: La acción incorrecta relacionada con "{user_suggested_topic}"
   - CONSECUENCIA: Daño concreto y descriptivo — NO inventes porcentajes ni cifras
   - SOLUCIÓN: Técnica específica y accionable

⚠️ FORMATO CRÍTICO:
- DEBES usar EXACTAMENTE este formato: "Error → Consecuencia → Solución"
- DEBES incluir los símbolos "→" para separar las tres partes
- El tema DEBE estar relacionado con: "{user_suggested_topic}"
- NO uses preguntas como "¿Sabías que...?" o "¿Te has preguntado...?"
- NO inventes porcentajes ("30%", "hasta 40%") — describe el daño sin cifras fabricadas

Ejemplos CORRECTOS:
- "Almacenar grano sin secar → Hongos arruinan lotes completos en clima húmedo → Secar a 14% de humedad antes de almacenar"
- "No calibrar la aspersora → Aplicación desigual desperdicia producto y deja zonas sin proteger → Calibrar antes de cada ciclo de aplicación"

RESPONDE SOLO CON JSON (sin markdown):
{{
  "topic": "Error específico → Consecuencia concreta y descriptiva → Solución técnica accionable (sobre {user_suggested_topic})",
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
        # Other days - use descriptive topic format appropriate to the day's theme
        if user_suggested_topic:
            prompt += f"""🔴 TEMA OBLIGATORIO DEL USUARIO: "{user_suggested_topic}"
El post DEBE ser sobre este tema. NO lo ignores ni lo reemplaces con otro.

"""
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
        # Tuesday/Thursday should use "Error → Daño → Solución" format
        if '→' not in topic_strategy.topic:
            try:
                import social_logging
                social_logging.safe_log_warning(
                    f"[TOPIC ENGINE] {day_name} topic missing '→' separators - should use 'Error → Daño → Solución' format",
                    topic=topic_strategy.topic,
                    day=day_name
                )
            except Exception:
                pass
        elif not validate_topic_format(topic_strategy.topic):
            try:
                import social_logging
                social_logging.safe_log_warning(
                    f"[TOPIC ENGINE] {day_name} topic format validation failed - expected 'Error → Daño → Solución'",
                    topic=topic_strategy.topic,
                    day=day_name
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
