"""
Social Media Deduplication Module
Handles topic-based deduplication using topic_hash as canonical unit.
Topic format: "Problema → Solución"
"""

from typing import List, Dict, Set, Any, Tuple, Optional
from datetime import datetime, timedelta, date as date_type
from sqlalchemy.orm import Session
from sqlalchemy import text
from models import SocialPost
from routes.social_topic import normalize_topic, compute_topic_hash, split_topic

import logging

logger = logging.getLogger(__name__)


def fetch_recent_posts(
    db: Session,
    date_obj: datetime,
    days_back: int = 10,
    limit: int = 20
) -> List[SocialPost]:
    """
    Fetch recent posts from the database, correctly ordered by creation time.
    
    Args:
        db: Database session
        date_obj: Reference date
        days_back: Number of days to look back
        limit: Maximum number of posts to return
    
    Returns:
        List of SocialPost objects, ordered by created_at DESC (most recent first)
    """
    cutoff_date = date_obj - timedelta(days=days_back)
    
    # Use DATE comparison properly (date_for is now DATE type)
    recent_posts = db.query(SocialPost).filter(
        SocialPost.date_for >= cutoff_date.date(),
        SocialPost.date_for <= date_obj.date()
    ).order_by(SocialPost.created_at.desc()).limit(limit).all()
    
    return recent_posts


def build_history_summary(
    recent_posts: List[SocialPost],
    batch_generated_history: List[str] = None
) -> str:
    """
    Build a human-readable history summary from recent posts.
    Uses explicit topic field, not caption inference.
    
    Args:
        recent_posts: List of recent SocialPost objects
        batch_generated_history: Optional list of batch-generated post summaries
    
    Returns:
        Formatted history string
    """
    history_items = []
    
    for p in recent_posts:
        # Use explicit topic field, not caption inference
        topic = p.topic if p.topic else "Sin tema"
        
        # Build history entry with all relevant info
        entry_parts = []
        if p.post_type:
            entry_parts.append(f"Tipo: {p.post_type}")
        if p.channel:
            entry_parts.append(f"Canal: {p.channel}")
        entry_parts.append(f"Tema: {topic}")
        if p.selected_product_id:
            entry_parts.append(f"Producto ID: {p.selected_product_id}")
        
        history_items.append(" | ".join(entry_parts))
    
    # Add batch history if present (posts generated just now in this session)
    if batch_generated_history:
        history_items.extend(batch_generated_history)
    
    return "\n- ".join(history_items) if history_items else "Sin historial previo."


def check_topic_duplicate(
    db: Session,
    topic: str,
    date_obj: date_type,
    days_back: int = 10
) -> Tuple[bool, Optional[SocialPost]]:
    """
    Check if a topic (by topic_hash) already exists within the last N days.
    
    Hard rule: Do NOT allow same topic_hash within last 10 days.
    
    Args:
        db: Database session
        topic: Topic string in format "Problema → Solución"
        date_obj: Reference date
        days_back: Number of days to look back (default 10)
    
    Returns:
        Tuple of (is_duplicate, existing_post)
    """
    normalized = normalize_topic(topic)
    topic_hash = compute_topic_hash(normalized)
    
    # Check both backwards AND forwards (important for batch generation with different date_for values)
    start_date = date_obj - timedelta(days=days_back)
    end_date = date_obj + timedelta(days=days_back)
    
    # Use indexed query on topic_hash
    existing = db.query(SocialPost).filter(
        SocialPost.topic_hash == topic_hash,
        SocialPost.date_for >= start_date,
        SocialPost.date_for <= end_date
    ).first()
    
    if existing:
        return True, existing
    return False, None


def check_problem_duplicate(
    db: Session,
    topic: str,
    date_obj: date_type,
    days_back: int = 3
) -> Tuple[bool, Optional[SocialPost]]:
    """
    Check if same problem (left side of →) appears with different solution within last N days.
    
    Soft rule: If same problem appears with different solution within last 3 days, block.
    
    Args:
        db: Database session
        topic: Topic string in format "Problema → Solución"
        date_obj: Reference date
        days_back: Number of days to look back (default 3)
    
    Returns:
        Tuple of (is_duplicate, existing_post)
    """
    normalized = normalize_topic(topic)
        error, _, solution = split_topic(normalized)
        problem = error  # For backward compatibility, use 'error' as 'problem'
    
    if not problem or len(problem) < 10:
        # Problem too short, skip soft check
        return False, None
    
    # Check both backwards AND forwards (important for batch generation with different date_for values)
    start_date = date_obj - timedelta(days=days_back)
    end_date = date_obj + timedelta(days=days_back)
    
    # Query posts in date range and check problem part
    # Use DB query to extract problem from topic
    recent_posts = db.query(SocialPost).filter(
        SocialPost.date_for >= start_date,
        SocialPost.date_for <= end_date
    ).all()
    
    # Check each post's problem part (normalize and compare)
    for post in recent_posts:
        if not post.topic:
            continue
        
        post_normalized = normalize_topic(post.topic)
        post_error, _, post_solution = split_topic(post_normalized)
        post_problem = post_error  # For backward compatibility
        
        # Compare normalized problems (simple string comparison)
        # For more sophisticated matching, could use similarity, but keeping it simple for now
        if post_problem == problem:
            # Same problem found - check if solution is different
            _, _, new_solution = split_topic(normalized)
            
            if post_solution != new_solution:
                # Same problem, different solution - block
                return True, post
    
    return False, None


def extract_deduplication_sets(
    recent_posts: List[SocialPost],
    dedup_context: Dict[str, Any] = None,
    used_in_batch: Dict[str, Any] = None
) -> Tuple[Set[str], Set[str], Set[str], List[str], Set[str], Set[str], Set[str]]:
    """
    Extract deduplication sets from recent posts.
    Returns product IDs, categories, channels, topics (explicit), and topic hashes.
    
    Returns:
        Tuple of (recent_product_ids, recent_categories, recent_channels, recent_topics, recent_topic_keywords, used_in_batch_ids, used_in_batch_categories)
    """
    recent_product_ids = set()
    recent_categories = set()
    recent_channels = set()
    recent_topics = []  # Explicit topics from topic field
    recent_topic_keywords = set()  # Keywords from topics (for display only, not dedupe)
    used_in_batch_ids = set()
    used_in_batch_categories = set()
    
    for p in recent_posts:
        if p.selected_product_id:
            recent_product_ids.add(str(p.selected_product_id))
        if p.channel:
            recent_channels.add(p.channel)
        
        # Use explicit topic field, not caption inference
        if p.topic:
            recent_topics.append(p.topic)  # Store full topic
            # Extract keywords for display/analysis (but not for dedupe)
            topic_lower = p.topic.lower()
            # Simple keyword extraction (for variety metrics only)
            words = topic_lower.split()
            keywords = [
                w for w in words
                if len(w) > 4
                and w not in ['para', 'con', 'del', 'las', 'los', 'una', 'uno', 'este', 'esta', 'estos', 'estas', 'problema', 'solución']
            ]
            recent_topic_keywords.update(keywords)
        
        # Also check formatted_content for product info (legacy support)
        if p.formatted_content and isinstance(p.formatted_content, dict):
            products = p.formatted_content.get('products', [])
            for prod in products:
                if isinstance(prod, dict):
                    if prod.get('id'):
                        recent_product_ids.add(str(prod['id']))
                    if prod.get('category'):
                        recent_categories.add(prod['category'])
    
    # Also use dedup context from frontend if provided (more comprehensive)
    if dedup_context:
        recent_product_ids.update(str(pid) for pid in dedup_context.get('recent_product_ids', []))
        recent_categories.update(dedup_context.get('recent_categories', []))
    
    # Check for products used in current batch (to avoid duplicates in same generation)
    if used_in_batch:
        used_in_batch_ids.update(str(pid) for pid in used_in_batch.get('product_ids', []))
        used_in_batch_categories.update(used_in_batch.get('categories', []))
    
    return (
        recent_product_ids,
        recent_categories,
        recent_channels,
        recent_topics,
        recent_topic_keywords,
        used_in_batch_ids,
        used_in_batch_categories
    )


def analyze_variety_metrics(
    recent_posts: List[SocialPost],
    batch_generated_history: List[str] = None
) -> Dict[str, Any]:
    """
    Analyze variety metrics to detect repetition and guide selection.
    Uses explicit topic field, not caption inference.
    
    Returns:
        Dictionary with variety analysis
    """
    recent_types = [p.post_type for p in recent_posts if p.post_type]
    recent_channels = [p.channel for p in recent_posts if p.channel]
    recent_topics = []  # Explicit topics
    recent_topic_keywords = set()
    
    # Extract topics from explicit topic field
    for p in recent_posts:
        if p.topic:
            recent_topics.append(p.topic)
            # Extract keywords for analysis (not for dedupe)
            topic_lower = p.topic.lower()
            words = topic_lower.split()
            keywords = [
                w for w in words
                if len(w) > 4
                and w not in ['para', 'con', 'del', 'las', 'los', 'una', 'uno', 'este', 'esta', 'estos', 'estas', 'problema', 'solución']
            ]
            recent_topic_keywords.update(keywords)
    
    # Count promos
    db_promo_count = sum(
        1 for t in recent_types
        if t and any(word in t.lower() for word in ['promo', 'venta', 'promoción'])
    )
    batch_promo_count = 0
    if batch_generated_history:
        batch_promo_count = sum(
            1 for item in batch_generated_history
            if any(word in item.lower() for word in ['promo', 'venta', 'promoción'])
        )
    
    total_recent = len(recent_types) + (len(batch_generated_history) if batch_generated_history else 0)
    promo_count = db_promo_count + batch_promo_count
    
    # More strict: penalize if > 20% are promos OR if last 2 posts were promos
    last_two_are_promo = (
        len(recent_types) >= 2
        and all(
            t and any(word in t.lower() for word in ['promo', 'venta', 'promoción'])
            for t in recent_types[:2]  # First 2 are most recent (ordered DESC)
        )
    )
    penalize_promo = (total_recent > 0 and (promo_count / total_recent) > 0.2) or last_two_are_promo
    
    # Analyze post type distribution
    from collections import Counter
    type_counter = Counter([t.lower() for t in recent_types if t])
    most_common_type = type_counter.most_common(1)[0][0] if type_counter else None
    most_common_count = type_counter.most_common(1)[0][1] if type_counter else 0
    
    # If same type used 2+ times in recent posts, warn
    type_repetition_warning = ""
    if most_common_count >= 2 and total_recent >= 2:
        type_repetition_warning = f"⛔ ALERTA: El tipo '{most_common_type}' se ha usado {most_common_count} veces recientemente. ELIGE UN TIPO DIFERENTE hoy.\n"
    
    # Also check if last 2 posts are the same type
    if len(recent_types) >= 2:
        last_two_types = [t.lower() if t else '' for t in recent_types[:2]]  # Most recent first
        if last_two_types[0] == last_two_types[1] and last_two_types[0] != '':
            type_repetition_warning += f"⛔ ALERTA: Los últimos 2 posts fueron del tipo '{last_two_types[0]}'. ESTÁ PROHIBIDO usar este tipo hoy.\n"
    
    # Analyze channel variety
    channel_counts = Counter(recent_channels)
    
    # Analyze topic variety (check for repeated topics using explicit topic field)
    topic_counts = Counter(recent_topics)
    
    # Check for over-focusing on specific topics (using explicit topics)
    calefaccion_count = sum(1 for t in recent_topics if 'calefacc' in t.lower() or 'calefacción' in t.lower())
    heladas_count = sum(1 for t in recent_topics if 'helada' in t.lower())
    invernadero_count = sum(1 for t in recent_topics if 'invernader' in t.lower())
    mantenimiento_count = sum(1 for t in recent_topics if 'mantenimiento' in t.lower())
    
    # Also check keywords for additional context
    calefaccion_count += sum(1 for k in recent_topic_keywords if 'calefacc' in k)
    heladas_count += sum(1 for k in recent_topic_keywords if 'helada' in k)
    invernadero_count += sum(1 for k in recent_topic_keywords if 'invernader' in k)
    mantenimiento_count += sum(1 for k in recent_topic_keywords if 'mantenimiento' in k)
    
    over_focus_warning = ""
    if calefaccion_count >= 2:
        over_focus_warning = f"⛔ ALERTA CRÍTICA: Ya se han generado {int(calefaccion_count)} posts sobre calefacción recientemente. ESTÁ PROHIBIDO usar este tema hoy. Busca otros temas relevantes para la temporada.\n"
    if heladas_count >= 3:
        over_focus_warning += f"⛔ ALERTA CRÍTICA: Ya se han generado {int(heladas_count)} posts sobre heladas recientemente. ESTÁ PROHIBIDO usar este tema hoy. Elige un tema completamente diferente.\n"
    if mantenimiento_count >= 3:
        over_focus_warning += f"⛔ ALERTA: Ya se han generado {int(mantenimiento_count)} posts sobre mantenimiento recientemente. Varía el tema significativamente.\n"
    if invernadero_count >= 5:
        over_focus_warning += f"⛔ ALERTA: Ya se han generado {int(invernadero_count)} posts sobre invernaderos recientemente. Considera otros temas agrícolas (campo abierto, ganadería, forestal, etc.).\n"
    
    return {
        "recent_types": recent_types,
        "recent_channels": recent_channels,
        "recent_topics": recent_topics,
        "recent_topic_keywords": recent_topic_keywords,
        "type_counter": type_counter,
        "channel_counts": dict(channel_counts),
        "topic_counts": dict(topic_counts),
        "promo_count": promo_count,
        "total_recent": total_recent,
        "penalize_promo": penalize_promo,
        "type_repetition_warning": type_repetition_warning,
        "over_focus_warning": over_focus_warning,
        "calefaccion_count": calefaccion_count,
        "heladas_count": heladas_count,
        "invernadero_count": invernadero_count,
        "mantenimiento_count": mantenimiento_count,
    }
