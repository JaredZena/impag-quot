"""
Structured Logging Module for Social Media Endpoints
Provides logging with redaction of sensitive data.
"""

import logging
import re
import sys
from typing import Any, Dict

# Configure structured logging
logger = logging.getLogger(__name__)

# Set up handler if not already configured
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Patterns for redaction (sensitive data)
REDACTION_PATTERNS = [
    (r'CLAUDE_API_KEY["\']?\s*[:=]\s*["\']?([^"\'\s]+)', 'CLAUDE_API_KEY=***REDACTED***'),
    (r'api[_-]?key["\']?\s*[:=]\s*["\']?([^"\'\s]+)', 'api_key=***REDACTED***'),
    (r'token["\']?\s*[:=]\s*["\']?([^"\'\s]+)', 'token=***REDACTED***'),
    (r'password["\']?\s*[:=]\s*["\']?([^"\'\s]+)', 'password=***REDACTED***'),
]


def redact_sensitive_data(text: str) -> str:
    """
    Redact sensitive data from log messages.
    
    Args:
        text: Text that may contain sensitive data
    
    Returns:
        Text with sensitive data redacted
    """
    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted


def safe_log_info(message: str, **kwargs):
    """
    Log info message with redaction of sensitive data.
    """
    redacted_message = redact_sensitive_data(message)
    redacted_kwargs = {k: redact_sensitive_data(str(v)) if isinstance(v, str) else v for k, v in kwargs.items()}
    
    # Format message with kwargs for better readability
    if redacted_kwargs:
        kwargs_str = " ".join([f"{k}={v}" for k, v in redacted_kwargs.items()])
        formatted_message = f"{redacted_message} | {kwargs_str}"
    else:
        formatted_message = redacted_message
    
    logger.info(formatted_message)


def safe_log_warning(message: str, **kwargs):
    """
    Log warning message with redaction of sensitive data.
    """
    redacted_message = redact_sensitive_data(message)
    redacted_kwargs = {k: redact_sensitive_data(str(v)) if isinstance(v, str) else v for k, v in kwargs.items()}
    
    # Format message with kwargs for better readability
    if redacted_kwargs:
        kwargs_str = " ".join([f"{k}={v}" for k, v in redacted_kwargs.items()])
        formatted_message = f"{redacted_message} | {kwargs_str}"
    else:
        formatted_message = redacted_message
    
    logger.warning(formatted_message)


def safe_log_error(message: str, exc_info: bool = False, **kwargs):
    """
    Log error message with redaction of sensitive data.
    """
    redacted_message = redact_sensitive_data(message)
    redacted_kwargs = {k: redact_sensitive_data(str(v)) if isinstance(v, str) else v for k, v in kwargs.items()}
    
    # Format message with kwargs for better readability
    if redacted_kwargs:
        kwargs_str = " ".join([f"{k}={v}" for k, v in redacted_kwargs.items()])
        formatted_message = f"{redacted_message} | {kwargs_str}"
    else:
        formatted_message = redacted_message
    
    logger.error(formatted_message, exc_info=exc_info)


def log_llm_request(endpoint: str, model: str, token_count: int = None, **kwargs):
    """
    Log LLM request without exposing sensitive prompt content.
    """
    safe_log_info(
        f"LLM request: {endpoint}",
        model=model,
        token_count=token_count,
        **kwargs
    )


def log_llm_response(endpoint: str, success: bool, error: str = None, **kwargs):
    """
    Log LLM response without exposing full response content.
    """
    if success:
        safe_log_info(f"LLM response: {endpoint} - Success", **kwargs)
    else:
        safe_log_error(f"LLM response: {endpoint} - Failed", error=error, **kwargs)


