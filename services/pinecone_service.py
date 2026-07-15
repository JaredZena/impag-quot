"""
Pinecone vector database service for document embeddings.
Uses the existing "impag" index with namespaces to separate document types.
"""
from pinecone import Pinecone
from typing import List, Dict, Optional
from config import pinecone_api_key

CATEGORY_TO_NAMESPACE = {
    'cotizacion': 'cotizaciones',
    'nota': 'notas',
    'factura': 'facturas',
    'comprobante-de-pago': 'comprobantes-de-pago',
    'project-image': 'project-images',
    'packaging-logistics': 'packaging-logistics',
    'whatsapp-chat': 'whatsapp-chats',
    'ficha-tecnica': 'fichas-tecnicas',
    'imagen-de-producto': 'imagenes-de-producto',
    'infografia': 'infografias',
    'article': 'articles',
    'control-de-ventas': 'control-de-ventas',
    'catalogo': 'catalogos',
    'estado-de-cuenta': 'estados-de-cuenta',
}

_pinecone_index = None
_pinecone_client = None

# Multilingual cross-encoder hosted by Pinecone. Reranking the merged
# cross-namespace candidates makes their scores actually comparable —
# raw ANN scores from different namespaces are not.
RERANK_MODEL = "bge-reranker-v2-m3"
# bge raw scores are uncalibrated and run low (correct answers often <0.1).
# A fixed floor measurably dropped true positives (eval: recall@7 0.505->0.404
# with floor=0.02), so reranking only reorders by default. Revisit once logged
# production feedback gives a score distribution to calibrate against.
RERANK_MIN_SCORE = None


def _get_client():
    global _pinecone_client
    if _pinecone_client is None:
        _pinecone_client = Pinecone(api_key=pinecone_api_key)
    return _pinecone_client


def _get_index():
    global _pinecone_index
    if _pinecone_index is None:
        _pinecone_index = _get_client().Index("impag")
    return _pinecone_index


def rerank_results(
    query_text: str,
    results: List[Dict],
    top_n: int,
    min_score: Optional[float] = RERANK_MIN_SCORE,
) -> List[Dict]:
    """
    Rerank candidate matches with Pinecone's hosted cross-encoder and drop
    everything below min_score. Falls back to vector-score order on any error
    so search never breaks when the inference API is unavailable.
    """
    if not results:
        return []

    # Pinecone metadata stores only the first ~1000 chars of each chunk.
    # Judge (and downstream, prompt-inject) the FULL chunk text from Postgres;
    # fall back to metadata text for vectors without a DocumentChunk row.
    full_texts = {}
    try:
        from models import SessionLocal, DocumentChunk
        ids = [r["id"] for r in results if r.get("id")]
        if ids:
            db = SessionLocal()
            try:
                rows = db.query(DocumentChunk.pinecone_vector_id, DocumentChunk.chunk_text) \
                         .filter(DocumentChunk.pinecone_vector_id.in_(ids)).all()
                full_texts = {vid: txt for vid, txt in rows if txt}
            finally:
                db.close()
    except Exception as e:
        print(f"Chunk hydration failed, reranking on metadata text: {e}")

    docs = []
    for r in results:
        md = r.get("metadata", {})
        full = full_texts.get(r.get("id"))
        if full:
            md["text"] = full  # un-truncate for consumers downstream too
        docs.append(str(full or md.get("text") or md.get("chunk_text") or "")[:2000])
    try:
        rr = _get_client().inference.rerank(
            model=RERANK_MODEL,
            query=query_text,
            documents=docs,
            top_n=min(top_n, len(docs)),
            return_documents=False,
        )
        ranked = []
        for row in rr.data:
            item = results[row.index]
            item["rerank_score"] = row.score
            if min_score is not None and row.score < min_score:
                continue
            ranked.append(item)
        return ranked
    except Exception as e:
        # Preserve the CALLER's ordering: for hybrid_search the input order is
        # the RRF fusion order; raw scores from different arms/namespaces are
        # not comparable, so re-sorting here would silently discard the fusion.
        print(f"Rerank failed, keeping caller's ranking: {e}")
        return results[:top_n]


def get_namespace_for_category(category: str) -> str:
    return CATEGORY_TO_NAMESPACE.get(category, 'general')


def upsert_document_chunks(
    file_id: int,
    category: str,
    chunks: List[Dict],
    embeddings: List[List[float]],
    metadata_base: Dict,
) -> List[str]:
    """
    Upsert chunk embeddings to Pinecone. Returns list of vector IDs.

    Vector ID format: "doc-{file_id}-chunk-{chunk_index}"
    Namespace: derived from file category.
    """
    index = _get_index()
    namespace = get_namespace_for_category(category)

    vectors = []
    vector_ids = []
    for chunk, embedding in zip(chunks, embeddings):
        vector_id = f"doc-{file_id}-chunk-{chunk['index']}"
        vector_ids.append(vector_id)

        metadata = {
            **{k: v for k, v in metadata_base.items() if v is not None},
            "file_id": file_id,
            "chunk_index": chunk["index"],
            "text": chunk["text"][:1000],  # Pinecone metadata limit ~40KB
            "category": category,
        }

        vectors.append({
            "id": vector_id,
            "values": embedding,
            "metadata": metadata,
        })

    # Upsert in batches of 100 (Pinecone best practice)
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        index.upsert(vectors=batch, namespace=namespace)

    return vector_ids


def delete_document_vectors(file_id: int, category: str, chunk_count: int = 0) -> None:
    """Delete all vectors for a document from Pinecone.

    Uses prefix-based delete (doc-{file_id}-chunk-) which catches all chunks
    regardless of chunk_count accuracy.
    Falls back to ID-based delete if prefix delete is not supported.
    """
    index = _get_index()
    namespace = get_namespace_for_category(category)

    try:
        index.delete(filter={"file_id": {"$eq": file_id}}, namespace=namespace)
    except Exception:
        # Fallback: delete by explicit IDs if metadata filter isn't supported
        if chunk_count > 0:
            vector_ids = [f"doc-{file_id}-chunk-{i}" for i in range(chunk_count)]
            index.delete(ids=vector_ids, namespace=namespace)


def search_vectors(
    query_embedding: List[float],
    namespaces: Optional[List[str]] = None,
    top_k: int = 10,
    filter_dict: Optional[Dict] = None,
    query_text: Optional[str] = None,
) -> List[Dict]:
    """
    Search across one or more namespaces in Pinecone.

    When query_text is provided, the merged cross-namespace candidate pool
    (top_k per namespace) is reranked with a hosted cross-encoder and cut to
    top_k with a relevance floor. Without it, legacy behavior: raw ANN scores
    sorted globally (scores from different namespaces are not comparable —
    prefer passing query_text).
    """
    index = _get_index()

    if namespaces is None:
        namespaces = list(CATEGORY_TO_NAMESPACE.values())

    all_results = []
    for ns in namespaces:
        try:
            response = index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                namespace=ns,
                filter=filter_dict,
            )
            for match in response.get("matches", []):
                all_results.append({
                    "id": match["id"],
                    "score": match["score"],
                    "metadata": match.get("metadata", {}),
                    "namespace": ns,
                })
        except Exception as e:
            print(f"Error querying namespace {ns}: {e}")
            continue

    # Pre-sort by raw score so rerank_results' input-order fallback degrades
    # to the legacy behavior for this caller.
    all_results.sort(key=lambda x: x["score"], reverse=True)
    if query_text:
        return rerank_results(query_text, all_results, top_n=top_k)
    return all_results[:top_k]
