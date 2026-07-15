"""
Hybrid document retrieval: dense vectors (Pinecone) + Spanish full-text
(Postgres tsvector) + filename trigram match, fused with Reciprocal Rank
Fusion. Optionally reranked with the hosted cross-encoder.

Dense-only retrieval systematically misses exact identifiers (SKUs like
COT-IMPAG-xxxx, invoice folios, customer names); the lexical arms exist
for precisely those queries.
"""
from typing import Dict, List, Optional

from sqlalchemy import text as sqltext

from models import SessionLocal
from services.pinecone_service import (
    CATEGORY_TO_NAMESPACE,
    get_namespace_for_category,
    rerank_results,
    search_vectors,
)

RRF_K = 60          # standard reciprocal-rank-fusion constant
FTS_POOL = 15       # candidates from the full-text arm
FILENAME_POOL = 5   # candidates from the filename arm

_NAMESPACE_TO_CATEGORY = {v: k for k, v in CATEGORY_TO_NAMESPACE.items()}


def _row_to_result(row) -> Dict:
    return {
        "id": row["vid"],
        "score": float(row["rank"] or 0),
        "metadata": {
            "file_id": row["file_id"],
            "original_filename": row["original_filename"],
            "category": row["category"],
            "text": (row["chunk_text"] or "")[:2000],
            "chunk_index": row["chunk_index"],
            "supplier_id": row["supplier_id"],
        },
        "namespace": get_namespace_for_category(row["category"]),
    }


def _fts_arm(db, query_text: str, top_n: int, categories, supplier_id) -> List[Dict]:
    sql = """
        SELECT dc.pinecone_vector_id AS vid, dc.chunk_text, dc.chunk_index,
               fm.id AS file_id, fm.original_filename, fm.category, fm.supplier_id,
               ts_rank(dc.chunk_tsv, websearch_to_tsquery('spanish', :q)) AS rank
        FROM document_chunk dc
        JOIN file_metadata fm ON fm.id = dc.file_id
        WHERE dc.chunk_tsv @@ websearch_to_tsquery('spanish', :q)
          AND fm.archived_at IS NULL
    """
    params = {"q": query_text, "n": top_n}
    if categories:
        sql += " AND fm.category = ANY(:cats)"
        params["cats"] = list(categories)
    if supplier_id is not None:
        sql += " AND fm.supplier_id = :sid"
        params["sid"] = supplier_id
    sql += " ORDER BY rank DESC LIMIT :n"
    return [_row_to_result(r) for r in db.execute(sqltext(sql), params).mappings()]


def _filename_arm(db, query_text: str, top_n: int, categories, supplier_id) -> List[Dict]:
    """Exact-identifier lookups: a query that IS a filename/SKU/folio should
    surface that document even when embeddings miss it entirely."""
    # % operator (not similarity()>x) so the trigram GIN index is usable;
    # threshold comes from pg_trgm.similarity_threshold (default 0.3).
    # ILIKE metacharacters in user input are escaped; similarity() keeps the
    # raw query so trigram matching is unaffected.
    q_like = query_text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    sql = """
        SELECT dc.pinecone_vector_id AS vid, dc.chunk_text, dc.chunk_index,
               fm.id AS file_id, fm.original_filename, fm.category, fm.supplier_id,
               similarity(fm.original_filename, :q) AS rank
        FROM file_metadata fm
        JOIN document_chunk dc ON dc.file_id = fm.id AND dc.chunk_index = 0
        WHERE fm.archived_at IS NULL
          AND (fm.original_filename ILIKE '%' || :q_like || '%'
               OR fm.original_filename % :q)
    """
    params = {"q": query_text, "q_like": q_like, "n": top_n}
    if categories:
        sql += " AND fm.category = ANY(:cats)"
        params["cats"] = list(categories)
    if supplier_id is not None:
        sql += " AND fm.supplier_id = :sid"
        params["sid"] = supplier_id
    sql += " ORDER BY rank DESC LIMIT :n"
    return [_row_to_result(r) for r in db.execute(sqltext(sql), params).mappings()]


def hybrid_search(
    query_text: str,
    query_embedding: List[float],
    top_k: int = 7,
    namespaces: Optional[List[str]] = None,
    filter_dict: Optional[Dict] = None,
    rerank: bool = True,
) -> List[Dict]:
    """
    Drop-in replacement for search_vectors that fuses three retrieval arms
    with RRF. Returns the same result shape as search_vectors.
    """
    supplier_id = None
    if filter_dict and isinstance(filter_dict.get("supplier_id"), dict):
        supplier_id = filter_dict["supplier_id"].get("$eq")

    # Dense arm (rerank happens after fusion, not here)
    dense = search_vectors(
        query_embedding=query_embedding,
        namespaces=namespaces,
        top_k=top_k,
        filter_dict=filter_dict,
    )

    # Lexical arms search Postgres by category. namespaces=None means "all".
    # Legacy namespaces ('' / 'general') have no category — dense covers those.
    categories = None
    skip_lexical = False
    if namespaces is not None:
        categories = [_NAMESPACE_TO_CATEGORY[ns] for ns in namespaces if ns in _NAMESPACE_TO_CATEGORY]
        if not categories:
            skip_lexical = True

    fts, by_name = [], []
    if not skip_lexical:
        db = SessionLocal()
        try:
            fts = _fts_arm(db, query_text, FTS_POOL, categories, supplier_id)
            by_name = _filename_arm(db, query_text, FILENAME_POOL, categories, supplier_id)
        except Exception as e:
            print(f"Lexical arms failed, dense-only fallback: {e}")
        finally:
            db.close()

    # Reciprocal Rank Fusion across the three arms
    fused: Dict[str, Dict] = {}
    for arm in (dense, fts, by_name):
        for rank, item in enumerate(arm, 1):
            entry = fused.setdefault(item["id"], {"result": item, "rrf": 0.0})
            entry["rrf"] += 1.0 / (RRF_K + rank)

    pool = sorted(fused.values(), key=lambda e: -e["rrf"])
    results = [e["result"] for e in pool]

    if rerank and results:
        ranked = rerank_results(query_text, results, top_n=top_k)
        # Identifier queries: a strong filename match IS the answer — never let
        # the cross-encoder (which judges chunk text, not filenames) evict or
        # outrank it. by_name is similarity DESC; prepending the qualifying
        # matches as a block keeps the strongest at rank 1 (a weaker promoted
        # match must never outrank a stronger one that survived reranking).
        strong = [r for r in by_name if float(r["score"] or 0) >= 0.55]
        strong_ids = {r["id"] for r in strong}
        ranked = strong + [r for r in ranked if r["id"] not in strong_ids]
        return ranked[:top_k]
    return results[:top_k]
