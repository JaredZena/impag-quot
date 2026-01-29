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


def split_topic(topic: str) -> Tuple[str, str, Optional[str]]:
    """
    Split topic into error, damage, and solution parts.
    
    Supports both formats:
    - Old: "Problema → Solución" (2 parts)
    - New: "Error → Daño concreto → Solución" (3 parts)
    
    Args:
        topic: Topic string in format "Error → Daño concreto → Solución" or "Problema → Solución"
        
    Returns:
        Tuple of (error/problem, damage/consequence, solution) - all normalized
        If old format (2 parts), damage will be None
    """
    normalized = normalize_topic(topic)
    
    if '→' not in normalized:
        # If no arrow, treat entire string as error, empty damage and solution
        return normalized, None, ""
    
    # Count arrows to determine format
    arrow_count = normalized.count('→')
    
    if arrow_count >= 2:
        # New format: "Error → Daño concreto → Solución"
        parts = normalized.split('→', 2)
        error = parts[0].strip() if len(parts) > 0 else ""
        damage = parts[1].strip() if len(parts) > 1 else ""
        solution = parts[2].strip() if len(parts) > 2 else ""
        return error, damage, solution
    else:
        # Old format: "Problema → Solución" (backward compatibility)
        parts = normalized.split('→', 1)
        problem = parts[0].strip() if len(parts) > 0 else ""
        solution = parts[1].strip() if len(parts) > 1 else ""
        return problem, None, solution


def validate_topic(topic: str) -> Tuple[bool, Optional[str]]:
    """
    Validate topic format and content.
    
    Preferred format: "Error → Daño concreto → Solución"
    Also accepts (backward compatibility): "Problema → Solución"
    
    Validation rules:
    - topic must contain at least one →
    - For 3-part format: error >= 8 chars, damage >= 10 chars, solution >= 8 chars
    - For 2-part format: problem >= 10 chars, solution >= 8 chars
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
        return False, "Topic must contain → (arrow). Format: 'Error → Daño concreto → Solución' or 'Problema → Solución'"
    
    # Split into parts
    error, damage, solution = split_topic(normalized)
    
    # Determine format based on damage presence
    is_3_part = damage is not None
    
    if is_3_part:
        # New format: "Error → Daño concreto → Solución"
        # Error must be at least 8 chars
        if len(error) < 8:
            return False, f"Error part too short ({len(error)} chars, minimum 8): '{error}'"
        
        # Damage must be at least 10 chars (this is the emotional hook)
        if len(damage) < 10:
            return False, f"Damage part too short ({len(damage)} chars, minimum 10): '{damage}'. This should show concrete consequences (e.g., 'Pierdes 40% de agua', 'Reduce producción 30%')"
        
        # Solution must be at least 8 chars
        if len(solution) < 8:
            return False, f"Solution part too short ({len(solution)} chars, minimum 8): '{solution}'"
        
        # Check for vague terms
        vague_terms = ['mejora', 'optimiza', 'mejor', 'bueno', 'buena']
        error_words = set(error.lower().split())
        damage_words = set(damage.lower().split())
        solution_words = set(solution.lower().split())
        
        # Damage should contain concrete numbers or specific consequences
        has_concrete_damage = any(char.isdigit() for char in damage) or any(word in damage.lower() for word in ['pierdes', 'pierde', 'pierden', 'reduce', 'reduces', 'reducen', 'aumenta', 'aumentan', 'causa', 'causan', 'provoca', 'provocan', 'mata', 'matan', 'destruye', 'destruyen', '%', 'porcentaje'])
        
        if not has_concrete_damage and len(damage) < 15:
            return False, f"Damage part should be more concrete. Include numbers, percentages, or specific consequences: '{damage}'. Example: 'Pierdes 40% de agua' or 'Reduce producción 30%'"
        
        # If error is only vague terms, reject
        if error_words.issubset(set(vague_terms)):
            return False, f"Error part is too vague (only contains generic terms): '{error}'"
        
        # If solution is only vague terms, reject
        if solution_words.issubset(set(vague_terms)):
            return False, f"Solution part is too vague (only contains generic terms): '{solution}'"
    else:
        # Old format: "Problema → Solución" (backward compatibility)
        problem = error  # In 2-part format, first part is stored in 'error' variable
        # Problem must be at least 10 chars
        if len(problem) < 10:
            return False, f"Problem part too short ({len(problem)} chars, minimum 10): '{problem}'"
        
        # Solution must be at least 8 chars
        if len(solution) < 8:
            return False, f"Solution part too short ({len(solution)} chars, minimum 8): '{solution}'"
        
        # Check for vague terms
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


