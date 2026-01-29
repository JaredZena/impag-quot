"""
Social Media LLM Module
Handles LLM calls with strict JSON parsing, retry logic, and validation.
Topic validation is CRITICAL - topic must be in format "Error → Daño concreto → Solución" (preferred) or "Problema → Solución" (backward compatible).
"""

import json
import re
import anthropic
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, ValidationError
from fastapi import HTTPException
from routes.social_topic import validate_topic

logger = logging.getLogger(__name__)


def repair_json_string(raw: str) -> str:
    """
    Attempt to fix common LLM JSON output errors before parsing.
    - Removes trailing commas before } or ]
    - Escapes unescaped newlines inside double-quoted strings (replaces \\n with literal \\n in output)
    - Escapes unescaped double quotes inside double-quoted strings
    """
    if not raw or not raw.strip():
        return raw
    
    # 1. Remove trailing commas before } or ] (invalid in JSON)
    repaired = re.sub(r',(\s*[}\]])', r'\1', raw)
    
    # 2. Inside double-quoted strings: escape literal newlines and unescaped double quotes
    result = []
    i = 0
    in_string = False
    escape_next = False
    quote_char = '"'
    
    while i < len(repaired):
        c = repaired[i]
        
        if escape_next:
            result.append(c)
            escape_next = False
            i += 1
            continue
        
        if c == '\\' and in_string:
            result.append(c)
            escape_next = True
            i += 1
            continue
        
        if c == quote_char and not escape_next:
            if in_string:
                in_string = False
                result.append(c)
                i += 1
                continue
            else:
                in_string = True
                result.append(c)
                i += 1
                continue
        
        if in_string:
            if c == '\n':
                result.append('\\n')
                i += 1
                continue
            if c == '\r':
                result.append('\\r')
                i += 1
                continue
            if c == '\t':
                result.append('\\t')
                i += 1
                continue
            if c == quote_char:
                # Unescaped quote inside string (should not happen if we track escape_next)
                result.append('\\"')
                i += 1
                continue
        
        result.append(c)
        i += 1
    
    return ''.join(result)


class ViralAngleResponse(BaseModel):
    """Strict schema for viral angle generation phase response."""
    hook_type: str  # "shock|curiosity|loss|authority|contrast"
    primary_trigger: str  # "fear|curiosity|greed|simplicity|ego"
    hook_sentence: str
    visual_concept: str
    curiosity_gap: str


class StrategyResponse(BaseModel):
    """Strict schema for strategy phase response."""
    problem_identified: str
    topic: str  # Must be in format "Error → Daño concreto → Solución" (preferred) or "Problema → Solución" (backward compatible)
    post_type: str
    channel: str
    content_tone: str  # Content tone: Motivational, Promotional, Technical, Educational, Problem-Solving, Seasonal, Humorous, etc. (REQUIRED, non-empty)
    preferred_category: Optional[str] = ""
    search_needed: bool = True
    search_keywords: Optional[str] = ""
    
    def __init__(self, **data):
        super().__init__(**data)
        # Ensure content_tone is never empty (validation)
        if not self.content_tone or not self.content_tone.strip():
            raise ValueError("content_tone cannot be empty")


class ContentResponse(BaseModel):
    """Strict schema for content generation phase response."""
    selected_category: Optional[str] = ""
    selected_product_id: Optional[str] = ""
    channel: str
    caption: str
    image_prompt: Optional[str] = None
    carousel_slides: Optional[list[str]] = None
    needs_music: bool = False
    posting_time: Optional[str] = None
    notes: Optional[str] = ""
    topic: Optional[str] = ""  # Must echo the same topic from strategy phase


def clean_json_text(text: str) -> str:
    """
    Extract JSON from text that may contain markdown code blocks or extra text.
    Handles cases where JSON is wrapped in ```json``` or has text before/after.
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
    
    # If we found a complete JSON object, extract it
    if start_idx != -1 and end_idx != -1:
        return text[start_idx:end_idx]
    
    # Fallback: return the whole text
    return text


def parse_json_with_retry(
    client: anthropic.Client,
    response_text: str,
    schema_class: type[BaseModel],
    retry_prompt: Optional[str] = None,
    max_retries: int = 1
) -> BaseModel:
    """
    Parse JSON response with strict validation and retry logic.
    
    Args:
        client: Anthropic client
        response_text: Raw response text from LLM
        schema_class: Pydantic model class for validation
        retry_prompt: Optional prompt to send on retry
        max_retries: Maximum number of retries
    
    Returns:
        Validated Pydantic model instance
    
    Raises:
        ValueError: If JSON cannot be parsed or validated after retries
    """
    cleaned_json = clean_json_text(response_text)
    
    for attempt in range(max_retries + 1):
        try:
            # Try parsing; on first attempt also try repaired JSON if raw fails
            try:
                data = json.loads(cleaned_json)
            except json.JSONDecodeError:
                repaired = repair_json_string(cleaned_json)
                data = json.loads(repaired)
            # Validate with Pydantic
            validated = schema_class(**data)
            return validated
        except json.JSONDecodeError as e:
            if attempt < max_retries and retry_prompt:
                logger.warning(f"JSON parse error (attempt {attempt + 1}): {e}. Retrying...")
                # Retry with fix prompt (include enough context for content LLM; schema_class hint for length)
                snippet_len = 2000 if schema_class.__name__ == "ContentResponse" else 500
                retry_response = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=500 if schema_class.__name__ != "ContentResponse" else 2500,
                    temperature=0.3,
                    system="You are a JSON formatter. Fix the JSON and output ONLY valid JSON, no other text. Inside strings use \\n for newlines and \\\" for quotes. No trailing commas.",
                    messages=[
                        {"role": "user", "content": f"{retry_prompt}\n\nInvalid JSON received:\n{cleaned_json[:snippet_len]}\n\nError: {str(e)}\n\nFix the JSON and output only valid JSON."}
                    ]
                )
                cleaned_json = clean_json_text(retry_response.content[0].text)
            else:
                logger.error(f"JSON parse failed after {attempt + 1} attempts: {e}")
                raise ValueError(f"Failed to parse JSON after {max_retries + 1} attempts: {str(e)}")
        except ValidationError as e:
            if attempt < max_retries and retry_prompt:
                logger.warning(f"Validation error (attempt {attempt + 1}): {e}. Retrying...")
                snippet_len = 2000 if schema_class.__name__ == "ContentResponse" else 500
                retry_response = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=500 if schema_class.__name__ != "ContentResponse" else 2500,
                    temperature=0.3,
                    system="You are a JSON formatter. Fix the JSON to match the required schema and output ONLY valid JSON, no other text. Inside strings use \\n for newlines and \\\" for quotes.",
                    messages=[
                        {"role": "user", "content": f"{retry_prompt}\n\nInvalid JSON structure:\n{cleaned_json[:snippet_len]}\n\nValidation errors: {str(e)}\n\nFix the JSON to match the required schema and output only valid JSON."}
                    ]
                )
                cleaned_json = clean_json_text(retry_response.content[0].text)
            else:
                logger.error(f"Validation failed after {attempt + 1} attempts: {e}")
                raise ValueError(f"Failed to validate JSON after {max_retries + 1} attempts: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error parsing JSON: {e}")
            raise ValueError(f"Unexpected error parsing JSON: {str(e)}")
    
    # Should never reach here, but just in case
    raise ValueError("Failed to parse JSON after all retries")


def call_viral_angle_llm(
    client: anthropic.Client,
    prompt: str
) -> ViralAngleResponse:
    """
    Call LLM for viral angle generation phase with strict JSON parsing.
    
    This phase generates hooks and psychological triggers to maximize
    scroll stop, retention, and engagement.
    
    Returns:
        Validated ViralAngleResponse
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=400,
            temperature=0.8,
            system="Eres un Growth Hacker especializado en viralización de contenido agrícola. Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional antes o después. No incluyas explicaciones, solo el JSON.",
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = response.content[0].text
        
        # Parse with retry
        retry_prompt = "Fix the JSON. Output only valid JSON matching this schema: {hook_type: string (one of: shock, curiosity, loss, authority, contrast), primary_trigger: string (one of: fear, curiosity, greed, simplicity, ego), hook_sentence: string, visual_concept: string, curiosity_gap: string}"
        
        return parse_json_with_retry(
            client,
            response_text,
            ViralAngleResponse,
            retry_prompt=retry_prompt,
            max_retries=1
        )
    except Exception as e:
        logger.error(f"Viral angle LLM call failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate viral angle: {str(e)}"
        )


def call_strategy_llm(
    client: anthropic.Client,
    prompt: str
) -> StrategyResponse:
    """
    Call LLM for strategy phase with strict JSON parsing and topic validation.
    
    Topic validation rules:
    - topic must contain →
    - problem part >= 10 chars
    - solution part >= 8 chars
    - reject vague topics
    
    Returns:
        Validated StrategyResponse with valid topic
    
    Raises:
        HTTPException(500): If topic validation fails after retry
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=300,
            temperature=0.5,
            system="Eres un cerebro estratégico. Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional antes o después. No incluyas explicaciones, solo el JSON. El campo 'topic' DEBE seguir el formato: 'Error → Daño concreto → Solución' (con 2 flechas →). Ejemplo: 'Regar por surco → Pierdes 40% de agua → Riego por goteo presurizado'.",
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = response.content[0].text
        
        # Parse with retry
        retry_prompt = "Fix the JSON. Output only valid JSON matching this schema: {problem_identified: string, topic: string (MUST be in format 'Error → Daño concreto → Solución' with 2 arrows →, e.g., 'Regar por surco → Pierdes 40% de agua → Riego por goteo presurizado'), post_type: string, channel: string, content_tone: string (one of: Motivational, Promotional, Technical, Educational, Problem-Solving, Seasonal, Humorous, Informative, Inspirational), preferred_category: string (optional), search_needed: boolean, search_keywords: string (optional)}"
        
        validated_response = parse_json_with_retry(
            client,
            response_text,
            StrategyResponse,
            retry_prompt=retry_prompt,
            max_retries=1
        )
        
        # Validate topic format
        is_valid, error_msg = validate_topic(validated_response.topic)
        if not is_valid:
            # Retry ONCE with topic validation instruction
            logger.warning(f"Topic validation failed: {error_msg}. Retrying with fix instruction...")
            fix_prompt = f"Fix topic format and return valid JSON only. The topic '{validated_response.topic}' is invalid: {error_msg}. Topic MUST be in format 'Error → Daño concreto → Solución' with 2 arrows →. Example: 'Regar por surco → Pierdes 40% de agua → Riego por goteo presurizado'. Error >= 8 chars, Damage >= 10 chars (must include concrete numbers/percentages), Solution >= 8 chars. Return only valid JSON."
            
            retry_response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=300,
                temperature=0.3,
                system="You are a JSON formatter. Fix the topic format and output ONLY valid JSON, no other text.",
                messages=[
                    {"role": "user", "content": f"{prompt}\n\n{fix_prompt}"}
                ]
            )
            retry_text = retry_response.content[0].text
            
            # Parse retry response
            validated_response = parse_json_with_retry(
                client,
                retry_text,
                StrategyResponse,
                retry_prompt=retry_prompt,
                max_retries=0  # No more retries
            )
            
            # Validate again
            is_valid, error_msg = validate_topic(validated_response.topic)
            if not is_valid:
                # Still invalid after retry - fail loudly
                logger.error(f"Topic validation failed after retry: {error_msg}. Topic: {validated_response.topic}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Topic validation failed: {error_msg}. Topic must be in format 'Problema → Solución'."
                )
        
        return validated_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Strategy LLM call failed: {e}", exc_info=True)
        # DO NOT silently fallback - fail loudly
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate strategy. Error: {str(e)}"
        )


def call_content_llm(
    client: anthropic.Client,
    prompt: str
) -> ContentResponse:
    """
    Call LLM for content generation phase with strict JSON parsing.
    
    Returns:
        Validated ContentResponse
    """
    try:
        response = client.messages.create(
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
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = response.content[0].text
        
        # Parse with retry (2 retries = 3 total attempts; content often has newlines/quotes in strings)
        retry_prompt = "Fix the JSON. Output only valid JSON. CRITICAL: Inside string values, use \\n for newlines (never real line breaks), and \\\" for quotes. No trailing commas before } or ]. Schema: {selected_category, selected_product_id, channel, caption, image_prompt, carousel_slides, needs_music, posting_time, notes, topic}."
        return parse_json_with_retry(
            client,
            response_text,
            ContentResponse,
            retry_prompt=retry_prompt,
            max_retries=2
        )
    except Exception as e:
        logger.error(f"Content LLM call failed: {e}", exc_info=True)
        # Return safe fallback - this should rarely happen, but if it does, return minimal valid response
        raise HTTPException(
            status_code=500,
            detail="Failed to generate content. Please try again."
        )
