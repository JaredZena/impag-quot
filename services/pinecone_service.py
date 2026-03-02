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


def _get_index():
    global _pinecone_index
    if _pinecone_index is None:
        pc = Pinecone(api_key=pinecone_api_key)
        _pinecone_index = pc.Index("impag")
    return _pinecone_index


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
) -> List[Dict]:
    """
    Search across one or more namespaces in Pinecone.
    Returns a unified list of matches sorted by score.
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

    all_results.sort(key=lambda x: x["score"], reverse=True)
    return all_results[:top_k]
