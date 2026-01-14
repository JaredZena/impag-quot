"""
Social Media Product Selection Module
Handles semantic search, product filtering, and selection logic.
"""

from typing import List, Dict, Any, Set, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import SupplierProduct, ProductCategory
from collections import Counter
import logging

logger = logging.getLogger(__name__)


def fetch_db_products(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch random active supplier products from the database with full details for ranking.
    Uses SupplierProduct table which has embeddings for semantic search.
    
    Uses PostgreSQL's random() function for true randomness.
    """
    db_products = (
        db.query(SupplierProduct)
        .join(ProductCategory, SupplierProduct.category_id == ProductCategory.id)
        .filter(
            SupplierProduct.is_active == True,
            SupplierProduct.archived_at == None
        )
        .order_by(func.random())  # PostgreSQL random() for true randomness
        .limit(limit)
        .all()
    )
    
    catalog = []
    for sp in db_products:
        cat_name = (
            sp.category.name
            if sp.category
            else (sp.product.category.name if sp.product and sp.product.category else "General")
        )
        catalog.append({
            "id": str(sp.id),
            "name": sp.name or (sp.product.name if sp.product else "Unknown"),
            "category": cat_name,
            "inStock": sp.stock > 0 if sp.stock is not None else False,
            "sku": sp.sku or (sp.product.sku if sp.product else ""),
            "description": sp.description or (sp.product.description if sp.product else "") or "",
            "specifications": sp.specifications or (sp.product.specifications if sp.product else {}) or {},
            "hasDescription": bool(
                (sp.description or (sp.product.description if sp.product else ""))
                and len((sp.description or (sp.product.description if sp.product else "")).strip()) > 20
            ),
            "hasSpecs": bool(
                (sp.specifications or (sp.product.specifications if sp.product else {}))
                and (
                    isinstance(sp.specifications or (sp.product.specifications if sp.product else {}), dict)
                    and len((sp.specifications or (sp.product.specifications if sp.product else {}))) > 0
                )
            )
        })
    return catalog


def search_products(
    db: Session,
    query: str,
    limit: int = 10,
    preferred_category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search supplier products using semantic search with embeddings when available,
    falling back to text search (ILIKE) if embeddings are not available.
    Uses SupplierProduct table which has embeddings for semantic search.
    """
    if not query:
        return fetch_db_products(db, limit)  # Fallback to random if no query
    
    # Try semantic search with embeddings first
    try:
        from rag_system_moved.embeddings import generate_embeddings
        query_embedding = generate_embeddings([query])[0]
        
        # Build query for supplier products with embeddings
        product_query = (
            db.query(SupplierProduct)
            .join(ProductCategory, SupplierProduct.category_id == ProductCategory.id)
            .filter(
                SupplierProduct.is_active == True,
                SupplierProduct.archived_at == None,
                SupplierProduct.embedding != None
            )
        )
        
        # Filter by preferred category if specified
        if preferred_category:
            product_query = product_query.filter(
                ProductCategory.name.ilike(f"%{preferred_category}%")
            )
        
        # Get top products by vector similarity
        db_products = (
            product_query.order_by(
                SupplierProduct.embedding.cosine_distance(query_embedding)
            )
            .limit(limit * 2)  # Get more candidates for filtering
            .all()
        )
        
        if db_products:
            # Convert to catalog format
            catalog = []
            for sp in db_products:
                cat_name = (
                    sp.category.name
                    if sp.category
                    else (sp.product.category.name if sp.product and sp.product.category else "General")
                )
                catalog.append({
                    "id": str(sp.id),
                    "name": sp.name or (sp.product.name if sp.product else "Unknown"),
                    "category": cat_name,
                    "inStock": sp.stock > 0 if sp.stock is not None else False,
                    "sku": sp.sku or (sp.product.sku if sp.product else ""),
                    "description": sp.description or (sp.product.description if sp.product else "") or "",
                    "specifications": sp.specifications or (sp.product.specifications if sp.product else {}) or {},
                    "hasDescription": bool(
                        (sp.description or (sp.product.description if sp.product else ""))
                        and len((sp.description or (sp.product.description if sp.product else "")).strip()) > 20
                    ),
                    "hasSpecs": bool(
                        (sp.specifications or (sp.product.specifications if sp.product else {}))
                        and (
                            isinstance(sp.specifications or (sp.product.specifications if sp.product else {}), dict)
                            and len((sp.specifications or (sp.product.specifications if sp.product else {}))) > 0
                        )
                    )
                })
            return catalog[:limit]  # Return only requested limit
    except Exception as e:
        logger.warning(f"Embedding search failed, falling back to text search: {e}")
    
    # Fallback to text search (ILIKE) if embeddings fail or no results
    terms = query.split()
    
    # Search supplier products by name (active only)
    product_query = (
        db.query(SupplierProduct)
        .join(ProductCategory, SupplierProduct.category_id == ProductCategory.id)
        .filter(
            SupplierProduct.is_active == True,
            SupplierProduct.archived_at == None,
            SupplierProduct.name.ilike(f"%{query}%")
        )
    )
    
    if preferred_category:
        product_query = product_query.filter(
            ProductCategory.name.ilike(f"%{preferred_category}%")
        )
    
    db_products = product_query.limit(limit).all()
    
    # If loose match needed:
    if not db_products and len(terms) > 0:
        # Fallback: search by first word
        product_query = (
            db.query(SupplierProduct)
            .join(ProductCategory, SupplierProduct.category_id == ProductCategory.id)
            .filter(
                SupplierProduct.is_active == True,
                SupplierProduct.archived_at == None,
                SupplierProduct.name.ilike(f"%{terms[0]}%")
            )
        )
        if preferred_category:
            product_query = product_query.filter(
                ProductCategory.name.ilike(f"%{preferred_category}%")
            )
        db_products = product_query.limit(limit).all()
    
    catalog = []
    for sp in db_products:
        cat_name = (
            sp.category.name
            if sp.category
            else (sp.product.category.name if sp.product and sp.product.category else "General")
        )
        catalog.append({
            "id": str(sp.id),
            "name": sp.name or (sp.product.name if sp.product else "Unknown"),
            "category": cat_name,
            "inStock": sp.stock > 0 if sp.stock is not None else False,
            "sku": sp.sku or (sp.product.sku if sp.product else ""),
            "description": sp.description or (sp.product.description if sp.product else "") or "",
            "specifications": sp.specifications or (sp.product.specifications if sp.product else {}) or {},
            "hasDescription": bool(
                (sp.description or (sp.product.description if sp.product else ""))
                and len((sp.description or (sp.product.description if sp.product else "")).strip()) > 20
            ),
            "hasSpecs": bool(
                (sp.specifications or (sp.product.specifications if sp.product else {}))
                and (
                    isinstance(sp.specifications or (sp.product.specifications if sp.product else {}), dict)
                    and len((sp.specifications or (sp.product.specifications if sp.product else {}))) > 0
                )
            )
        })
    return catalog


def filter_products_by_deduplication(
    candidate_products: List[SupplierProduct],
    recent_product_ids: Set[str],
    recent_categories: Set[str],
    used_in_batch_ids: Set[str],
    used_in_batch_categories: Set[str]
) -> List[SupplierProduct]:
    """
    Filter candidate products to avoid duplicates.
    
    Uses Counter for proper category counting (fixes the bug where set comparison
    was impossible).
    """
    filtered_candidates = []
    category_counter = Counter(recent_categories)
    
    for sp in candidate_products:
        sp_id_str = str(sp.id)
        # Skip if used recently
        if sp_id_str in recent_product_ids or sp_id_str in used_in_batch_ids:
            continue
        
        # Skip if category was heavily used (using Counter for proper counting)
        cat_name = (
            sp.category.name
            if sp.category
            else (sp.product.category.name if sp.product and sp.product.category else "General")
        )
        category_count = category_counter.get(cat_name.lower(), 0)
        if category_count >= 3:  # Fixed: now uses Counter properly
            continue
        
        filtered_candidates.append(sp)
    
    return filtered_candidates


def select_product_for_post(
    db: Session,
    search_query: str,
    preferred_category: Optional[str] = None,
    recent_product_ids: Set[str] = None,
    recent_categories: Set[str] = None,
    used_in_batch_ids: Set[str] = None,
    used_in_batch_categories: Set[str] = None
) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """
    Select a product for a social post, avoiding duplicates.
    
    Returns:
        Tuple of (selected_product_id, selected_category, product_details)
    """
    if recent_product_ids is None:
        recent_product_ids = set()
    if recent_categories is None:
        recent_categories = set()
    if used_in_batch_ids is None:
        used_in_batch_ids = set()
    if used_in_batch_categories is None:
        used_in_batch_categories = set()
    
    selected_product_id = None
    selected_category = None
    product_details = None
    
    # Use semantic search with embeddings
    try:
        from rag_system_moved.embeddings import generate_embeddings
        query_embedding = generate_embeddings([search_query])[0]
        
        # Build query for supplier products with embeddings
        product_query = (
            db.query(SupplierProduct)
            .join(ProductCategory, SupplierProduct.category_id == ProductCategory.id)
            .filter(
                SupplierProduct.is_active == True,
                SupplierProduct.archived_at == None,
                SupplierProduct.embedding != None
            )
        )
        
        # Filter by preferred category if specified
        if preferred_category:
            product_query = product_query.filter(
                ProductCategory.name.ilike(f"%{preferred_category}%")
            )
        
        # Get top products by vector similarity
        candidate_products = (
            product_query.order_by(
                SupplierProduct.embedding.cosine_distance(query_embedding)
            )
            .limit(30)
            .all()
        )
        
        # Filter out recently used products
        filtered_candidates = filter_products_by_deduplication(
            candidate_products,
            recent_product_ids,
            recent_categories,
            used_in_batch_ids,
            used_in_batch_categories
        )
        
        # If filtering removed everything, allow some repeats (but not from current batch)
        if not filtered_candidates:
            for sp in candidate_products[:10]:
                sp_id_str = str(sp.id)
                if sp_id_str not in used_in_batch_ids:
                    filtered_candidates.append(sp)
                    break
        
        # Select the best product (first in similarity-ordered list after filtering)
        if filtered_candidates:
            selected_sp = filtered_candidates[0]
            selected_product_id = str(selected_sp.id)
            selected_category = (
                selected_sp.category.name
                if selected_sp.category
                else (selected_sp.product.category.name if selected_sp.product and selected_sp.product.category else "General")
            )
            
            # Fetch full product details
            cost = float(selected_sp.cost or 0)
            shipping = float(selected_sp.shipping_cost_direct or 0)
            margin = float(selected_sp.default_margin or 0.30)  # Default 30% margin
            price = (cost + shipping) / (1 - margin) if margin < 1 else cost + shipping
            
            product_details = {
                "id": str(selected_sp.id),
                "name": selected_sp.name or (selected_sp.product.name if selected_sp.product else "Unknown"),
                "category": selected_category,
                "sku": selected_sp.sku or (selected_sp.product.sku if selected_sp.product else ""),
                "inStock": selected_sp.stock > 0 if selected_sp.stock is not None else False,
                "price": price
            }
    
    except Exception as e:
        logger.warning(f"Embedding-based product selection failed: {e}")
        # Fallback to text search
        found_products = search_products(db, search_query, limit=10, preferred_category=preferred_category)
        
        # Filter and select
        for p in found_products:
            if (
                p['id'] not in recent_product_ids
                and p['id'] not in used_in_batch_ids
            ):
                selected_product_id = p['id']
                selected_category = p['category']
                break
    
    return selected_product_id, selected_category, product_details


