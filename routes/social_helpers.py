"""
Helper functions for social content generation.
"""
from datetime import date, timedelta
from typing import List
from sqlalchemy.orm import Session
from models import SocialPost


def get_recent_topics(db: Session, lookback_days: int = 14, limit: int = 10) -> List[str]:
    """
    Get recent topics from database for variety guidance.

    Args:
        db: Database session
        lookback_days: How many days to look back
        limit: Max number of topics to return

    Returns:
        List of topic strings from recent posts
    """
    cutoff_date = date.today() - timedelta(days=lookback_days)

    recent_posts = db.query(SocialPost)\
        .filter(SocialPost.created_at >= cutoff_date)\
        .filter(SocialPost.topic.isnot(None))\
        .order_by(SocialPost.created_at.desc())\
        .limit(limit)\
        .all()

    return [post.topic for post in recent_posts if post.topic]


def get_recent_channels(db: Session, limit: int = 5) -> List[str]:
    """
    Get recent channels from database for variety.

    Args:
        db: Database session
        limit: Max number of channels to return

    Returns:
        List of channel strings from recent posts
    """
    recent_posts = db.query(SocialPost)\
        .filter(SocialPost.channel.isnot(None))\
        .order_by(SocialPost.created_at.desc())\
        .limit(limit)\
        .all()

    return [post.channel for post in recent_posts if post.channel]


def format_recent_topics_for_prompt(topics: List[str]) -> str:
    """
    Format recent topics for inclusion in prompt.

    Args:
        topics: List of topic strings

    Returns:
        Formatted string for prompt
    """
    if not topics:
        return "No hay temas recientes.\n"

    formatted = "TEMAS RECIENTES (ÚLTIMOS 14 DÍAS) - ELIGE ALGO DIFERENTE:\n"
    for topic in topics:
        formatted += f"- {topic}\n"

    return formatted


def format_recent_channels_for_prompt(channels: List[str]) -> str:
    """
    Format recent channels for inclusion in prompt.

    Args:
        channels: List of channel strings

    Returns:
        Formatted string for prompt
    """
    if not channels:
        return "No hay canales recientes.\n"

    formatted = "CANALES USADOS RECIENTEMENTE:\n"
    for channel in channels:
        formatted += f"- {channel}\n"

    return formatted


def compress_product_details(product: dict) -> str:
    """
    Compress product details to essential information for prompt.

    Args:
        product: Full product dict from database

    Returns:
        Brief product description string
    """
    if not product:
        return ""

    # Extract only essential details
    brief = f"""PRODUCTO SELECCIONADO:
- Nombre: {product.get('name', 'N/A')}
- Categoría: {product.get('category', 'N/A')}"""

    # Add 2-3 key features if available
    features = product.get('features', [])
    if features:
        brief += "\n- Características clave: " + ", ".join(features[:3])

    return brief
