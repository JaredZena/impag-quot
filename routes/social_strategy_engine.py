"""
Strategy Engine: Decides post type, tone, channel.

This module handles STEP 2 of the multi-step pipeline:
- Input: topic from Topic Engine, weekday theme, recent channels
- Output: post_type, tone, channel, search_needed, category, keywords
- Prompt size: ~600 tokens
"""
from pydantic import BaseModel
from typing import Optional
import anthropic
import json
import re


class ContentStrategy(BaseModel):
    """Output from Strategy Engine."""
    post_type: str
    tone: str
    channel: str
    search_needed: bool
    preferred_category: Optional[str] = ""
    search_keywords: Optional[str] = ""


def generate_content_strategy(
    client: anthropic.Anthropic,
    topic_strategy,  # TopicStrategy object from Topic Engine
    weekday_theme: dict,
    recent_channels: list
) -> ContentStrategy:
    """
    Generate content strategy using LLM.

    Args:
        client: Anthropic client
        topic_strategy: TopicStrategy from Topic Engine
        weekday_theme: Weekday theme dict from config
        recent_channels: List of recent channel strings

    Returns:
        ContentStrategy object with post_type, tone, channel, etc.
    """
    # Build compact prompt (~600 tokens)
    prompt = f"""Decide la estrategia de contenido para este tema.

TEMA IDENTIFICADO: {topic_strategy.topic}
PROBLEMA: {topic_strategy.problem_identified}
ÁNGULO: {topic_strategy.angle}
AUDIENCIA: {topic_strategy.target_audience}

PLAN SEMANAL:
Día: {weekday_theme['day_name']}
Tema del día: {weekday_theme['theme']}
Tipos de post recomendados: {', '.join(weekday_theme['recommended_post_types'])}

"""

    # Add recent channels for variety
    if recent_channels:
        prompt += "CANALES USADOS RECIENTEMENTE:\n"
        for ch in recent_channels[:5]:
            prompt += f"- {ch}\n"
        prompt += "\n⚠️ Elige un canal DIFERENTE al usado ayer (varía entre fb-post, tiktok, wa-status, ig-reel, etc.)\n\n"
    else:
        prompt += "No hay canales recientes.\n\n"

    # Add weekday-specific rules
    prompt += "REGLAS PARA ESTE DÍA:\n"

    if weekday_theme['day_name'] == 'Tuesday':
        prompt += """💸 MARTES = DÍA DE PROMOCIONES:
- search_needed DEBE ser SIEMPRE true (OBLIGATORIO)
- DEBES especificar preferred_category (ej: riego, fertilizantes, mallasombra, herramientas, sustratos)
- DEBES proporcionar search_keywords para buscar productos
- El post debe enfocarse en promocionar o destacar productos

"""
    elif weekday_theme['day_name'] in ['Monday', 'Wednesday', 'Saturday', 'Sunday']:
        prompt += """📚 DÍA EDUCATIVO/INFORMATIVO:
- search_needed puede ser false
- Solo busca producto si el tema lo requiere naturalmente
- Enfoque en educar, informar, motivar o inspirar

"""
    else:  # Thursday, Friday
        prompt += """🔧 DÍA FLEXIBLE:
- search_needed = true si el tema menciona productos específicos o soluciones con productos
- search_needed = false si es contenido educativo general sin producto específico

"""

    # Add available options
    prompt += """TU TAREA:
1. Elige el TIPO DE POST que mejor comunique este tema
2. Selecciona el TONO apropiado para el día y tema
3. Elige un CANAL diferente al usado recientemente
4. Decide si necesitas buscar producto

TIPOS DE POST DISPONIBLES:
- Infografías, Memes/tips rápidos, Kits, Promoción puntual, Tutorial corto,
  Caso de éxito, Antes/Después, FAQ/Mitos, Pro Tip, Checklist operativo, etc.

TONOS DISPONIBLES:
- Motivational, Promotional, Technical, Educational, Problem-Solving,
  Seasonal, Humorous, Informative, Inspirational

CANALES DISPONIBLES:
- fb-post, ig-post, tiktok, wa-status, fb-reel, ig-reel, wa-broadcast

RESPONDE SOLO CON JSON (sin markdown):
{
  "post_type": "nombre exacto del tipo (ej: Infografías, Memes/tips rápidos)",
  "tone": "tono apropiado (ej: Educational, Motivational)",
  "channel": "canal diferente al reciente (ej: fb-post, tiktok)",
  "search_needed": true o false,
  "preferred_category": "categoría de producto si search_needed=true (ej: riego, fertilizantes), vacío si no",
  "search_keywords": "términos de búsqueda si search_needed=true (ej: sistema riego goteo), vacío si no"
}
"""

    # Log the prompt (for debugging)
    try:
        import social_logging
        social_logging.safe_log_info(
            "[STRATEGY ENGINE] Prompt built",
            prompt_length=len(prompt),
            prompt_tokens_estimate=len(prompt) // 4,
            full_prompt=prompt
        )
    except Exception:
        pass  # Logging failure shouldn't break generation

    # Call LLM
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse JSON from response
    content = response.content[0].text.strip()

    # Log raw LLM response
    try:
        import social_logging
        social_logging.safe_log_info(
            "[STRATEGY ENGINE] LLM response received",
            response_length=len(content),
            raw_response=content
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

    strategy = ContentStrategy(**data)

    # Apply Tuesday constraint: always require product search
    if weekday_theme['day_name'] == 'Tuesday':
        strategy.search_needed = True
        # If LLM didn't provide category/keywords, that's okay - product selection will handle it

    # Log parsed result
    try:
        import social_logging
        social_logging.safe_log_info(
            "[STRATEGY ENGINE] Strategy generated successfully",
            post_type=strategy.post_type,
            tone=strategy.tone,
            channel=strategy.channel,
            search_needed=strategy.search_needed,
            preferred_category=strategy.preferred_category,
            search_keywords=strategy.search_keywords
        )
    except Exception:
        pass

    return strategy
