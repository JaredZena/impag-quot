"""
Social Media LLM Module
Handles LLM calls with strict JSON parsing, retry logic, and validation.
Topic validation is CRITICAL - topic must be in format "Problema → Solución".
"""

import json
import anthropic
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, ValidationError
from fastapi import HTTPException
from routes.social_topic import validate_topic

logger = logging.getLogger(__name__)


class StrategyResponse(BaseModel):
    """Strict schema for strategy phase response."""
    problem_identified: str
    topic: str  # Must be in format "Problema → Solución"
    post_type: str
    channel: str
    preferred_category: Optional[str] = ""
    search_needed: bool = True
    search_keywords: Optional[str] = ""


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
            data = json.loads(cleaned_json)
            # Validate with Pydantic
            validated = schema_class(**data)
            return validated
        except json.JSONDecodeError as e:
            if attempt < max_retries and retry_prompt:
                logger.warning(f"JSON parse error (attempt {attempt + 1}): {e}. Retrying...")
                # Retry with fix prompt
                retry_response = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=500,
                    temperature=0.3,
                    system="You are a JSON formatter. Fix the JSON and output ONLY valid JSON, no other text.",
                    messages=[
                        {"role": "user", "content": f"{retry_prompt}\n\nInvalid JSON received:\n{cleaned_json[:500]}\n\nError: {str(e)}\n\nFix the JSON and output only valid JSON."}
                    ]
                )
                cleaned_json = clean_json_text(retry_response.content[0].text)
            else:
                logger.error(f"JSON parse failed after {attempt + 1} attempts: {e}")
                raise ValueError(f"Failed to parse JSON after {max_retries + 1} attempts: {str(e)}")
        except ValidationError as e:
            if attempt < max_retries and retry_prompt:
                logger.warning(f"Validation error (attempt {attempt + 1}): {e}. Retrying...")
                # Retry with fix prompt
                retry_response = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=500,
                    temperature=0.3,
                    system="You are a JSON formatter. Fix the JSON to match the required schema and output ONLY valid JSON, no other text.",
                    messages=[
                        {"role": "user", "content": f"{retry_prompt}\n\nInvalid JSON structure:\n{cleaned_json[:500]}\n\nValidation errors: {str(e)}\n\nFix the JSON to match the required schema and output only valid JSON."}
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
            system="Eres un cerebro estratégico. Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional antes o después. No incluyas explicaciones, solo el JSON. El campo 'topic' DEBE seguir el formato exacto: 'Problema → Solución' (con flecha →).",
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = response.content[0].text
        
        # Parse with retry
        retry_prompt = "Fix the JSON. Output only valid JSON matching this schema: {problem_identified: string, topic: string (MUST be in format 'Problema → Solución' with arrow →), post_type: string, channel: string, preferred_category: string (optional), search_needed: boolean, search_keywords: string (optional)}"
        
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
            fix_prompt = f"Fix topic format and return valid JSON only. The topic '{validated_response.topic}' is invalid: {error_msg}. Topic MUST be in format 'Problema → Solución' with problem >= 10 chars and solution >= 8 chars. Return only valid JSON."
            
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
        
        # Parse with retry
        retry_prompt = "Fix the JSON. Output only valid JSON matching this schema: {selected_category: string (optional), selected_product_id: string (optional), channel: string, caption: string, image_prompt: string (optional), carousel_slides: array of strings (optional), needs_music: boolean, posting_time: string (optional), notes: string (optional)}"
        return parse_json_with_retry(
            client,
            response_text,
            ContentResponse,
            retry_prompt=retry_prompt,
            max_retries=1
        )
    except Exception as e:
        logger.error(f"Content LLM call failed: {e}", exc_info=True)
        # Return safe fallback - this should rarely happen, but if it does, return minimal valid response
        raise HTTPException(
            status_code=500,
            detail="Failed to generate content. Please try again."
        )


from fastapi import HTTPException

