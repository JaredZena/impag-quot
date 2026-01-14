"""
Rate Limiting Module for Social Media Endpoints
In-memory rate limiter with TODO for Redis migration.
"""

from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# In-memory rate limit storage
# Structure: {user_id: [(timestamp, endpoint), ...]}
_rate_limit_store: Dict[str, list[Tuple[datetime, str]]] = defaultdict(list)

# Rate limit configuration
RATE_LIMITS = {
    "/generate": {
        "max_requests": 20,  # Conservative default
        "window_seconds": 3600  # 1 hour
    },
    "/save": {
        "max_requests": 100,
        "window_seconds": 3600
    }
}


def check_rate_limit(user_id: str, endpoint: str) -> Tuple[bool, Optional[str]]:
    """
    Check if user has exceeded rate limit for an endpoint.
    
    Args:
        user_id: User identifier (from auth token)
        endpoint: Endpoint path (e.g., "/generate")
    
    Returns:
        Tuple of (allowed: bool, error_message: Optional[str])
    """
    if endpoint not in RATE_LIMITS:
        # No rate limit for this endpoint
        return True, None
    
    limit_config = RATE_LIMITS[endpoint]
    max_requests = limit_config["max_requests"]
    window_seconds = limit_config["window_seconds"]
    
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=window_seconds)
    
    # Get user's request history
    user_history = _rate_limit_store[user_id]
    
    # Filter to only requests within the window
    recent_requests = [
        (ts, ep) for ts, ep in user_history
        if ts >= window_start and ep == endpoint
    ]
    
    # Update stored history (keep only recent requests)
    _rate_limit_store[user_id] = recent_requests
    
    # Check if limit exceeded
    if len(recent_requests) >= max_requests:
        # Calculate time until next request allowed
        oldest_request = min(ts for ts, _ in recent_requests)
        next_allowed = oldest_request + timedelta(seconds=window_seconds)
        wait_seconds = int((next_allowed - now).total_seconds())
        
        error_msg = (
            f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds // 60} minutes. "
            f"Please wait {wait_seconds} seconds before trying again."
        )
        logger.warning(f"Rate limit exceeded for user {user_id} on {endpoint}")
        return False, error_msg
    
    # Record this request
    recent_requests.append((now, endpoint))
    _rate_limit_store[user_id] = recent_requests
    
    return True, None


def record_request(user_id: str, endpoint: str):
    """
    Record a request for rate limiting purposes.
    This is called after successful request processing.
    """
    now = datetime.utcnow()
    _rate_limit_store[user_id].append((now, endpoint))
    
    # Cleanup old entries (keep only last hour of data per user)
    window_start = now - timedelta(seconds=3600)
    _rate_limit_store[user_id] = [
        (ts, ep) for ts, ep in _rate_limit_store[user_id]
        if ts >= window_start
    ]


# TODO: Migrate to Redis for distributed rate limiting
# Example Redis implementation:
# - Use Redis sorted sets with timestamps as scores
# - Key: f"rate_limit:{user_id}:{endpoint}"
# - Use ZREMRANGEBYSCORE to clean old entries
# - Use ZCARD to count requests in window
# - Use TTL to auto-expire keys

