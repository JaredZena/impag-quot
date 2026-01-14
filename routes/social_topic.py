"""
Social Media Topic Module
Handles topic normalization, hashing, and validation.
Topic is the canonical unit of deduplication.
"""

import re
import hashlib
from typing import Tuple, Optional


def normalize_topic(topic: str) -> str:
    """
    Normalize a topic string to a canonical form.
    
    Rules:
    - lowercase
    - trim whitespace
    - collapse multiple spaces
    - normalize arrows: ->, =>, ➜, ➡ into →
    - remove emojis and punctuation at start/end
    - ensure exactly one space on each side of →
    
    Example:
    "🔥 Heladas queman plántulas  ->  Manta térmica"
    becomes
    "heladas queman plántulas → manta térmica"
    
    Args:
        topic: Raw topic string
        
    Returns:
        Normalized topic string
    """
    if not topic:
        return ""
    
    # Convert to lowercase
    normalized = topic.lower()
    
    # Remove emojis (keep only alphanumeric, spaces, arrows, and basic punctuation)
    # First, normalize arrow variations
    normalized = re.sub(r'[-=]+\s*>', '→', normalized)
    normalized = re.sub(r'➜|➡', '→', normalized)
    
    # Remove emojis (Unicode emoji ranges)
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "]+",
        flags=re.UNICODE
    )
    normalized = emoji_pattern.sub('', normalized)
    
    # Trim whitespace
    normalized = normalized.strip()
    
    # Collapse multiple spaces into single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Ensure exactly one space on each side of →
    normalized = re.sub(r'\s*→\s*', ' → ', normalized)
    normalized = normalized.strip()
    
    # Remove punctuation at start/end (but keep →)
    normalized = re.sub(r'^[^\w→]+|[^\w→]+$', '', normalized)
    normalized = normalized.strip()
    
    return normalized


def compute_topic_hash(topic: str) -> str:
    """
    Compute SHA256 hash of normalized topic.
    
    Args:
        topic: Topic string (will be normalized)
        
    Returns:
        SHA256 hash as hex string (64 characters)
    """
    normalized = normalize_topic(topic)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def split_topic(topic: str) -> Tuple[str, str]:
    """
    Split topic into problem and solution parts.
    
    Args:
        topic: Topic string in format "Problema → Solución"
        
    Returns:
        Tuple of (problem, solution) - both normalized
    """
    normalized = normalize_topic(topic)
    
    if '→' not in normalized:
        # If no arrow, treat entire string as problem, empty solution
        return normalized, ""
    
    parts = normalized.split('→', 1)
    problem = parts[0].strip() if len(parts) > 0 else ""
    solution = parts[1].strip() if len(parts) > 1 else ""
    
    return problem, solution


def validate_topic(topic: str) -> Tuple[bool, Optional[str]]:
    """
    Validate topic format and content.
    
    Validation rules:
    - topic must contain →
    - problem part >= 10 chars
    - solution part >= 8 chars
    - reject vague topics ("mejora", "optimiza" alone)
    
    Args:
        topic: Topic string to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not topic:
        return False, "Topic is empty"
    
    normalized = normalize_topic(topic)
    
    # Must contain arrow
    if '→' not in normalized:
        return False, "Topic must contain → (arrow) separating problem and solution"
    
    # Split into problem and solution
    problem, solution = split_topic(normalized)
    
    # Problem must be at least 10 chars
    if len(problem) < 10:
        return False, f"Problem part too short ({len(problem)} chars, minimum 10): '{problem}'"
    
    # Solution must be at least 8 chars
    if len(solution) < 8:
        return False, f"Solution part too short ({len(solution)} chars, minimum 8): '{solution}'"
    
    # Check for vague terms (if topic is ONLY vague terms, reject)
    vague_terms = ['mejora', 'optimiza', 'mejor', 'bueno', 'buena']
    problem_words = set(problem.lower().split())
    solution_words = set(solution.lower().split())
    
    # If problem is only vague terms, reject
    if problem_words.issubset(set(vague_terms)):
        return False, f"Problem part is too vague (only contains generic terms): '{problem}'"
    
    # If solution is only vague terms, reject
    if solution_words.issubset(set(vague_terms)):
        return False, f"Solution part is too vague (only contains generic terms): '{solution}'"
    
    return True, None


