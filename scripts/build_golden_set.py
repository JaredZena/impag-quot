#!/usr/bin/env python3
"""
Build the RAG golden evaluation set from real usage data (READ-ONLY).

Sources:
  1. query.query_text        — real user queries from the /query endpoint
  2. quotation.user_query    — queries that produced actual quotations
  3. Lexical adversarial cases — exact-filename lookups (COT-*, facturas)
     where ground truth is automatic: the file the name came from.

Output: eval/golden_set.json
  [{id, query, type: "semantic"|"lexical",
    expected_file_ids: [int],      # lexical ground truth (auto)
    relevant_vector_ids: [str],    # semantic ground truth (filled by rag_eval.py --annotate)
    source}]

Usage: python scripts/build_golden_set.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from models import SessionLocal

OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "golden_set.json")

NOISE = re.compile(r"^(test|prueba|hola|asdf|\.+)$", re.IGNORECASE)


def main():
    db = SessionLocal()
    db.execute(text("SET default_transaction_read_only = on"))

    cases = []
    seen = set()

    def add(query, type_, source, expected_file_ids=None):
        q = " ".join(query.split()).strip()
        key = q.lower()
        if len(q) < 10 or NOISE.match(q) or key in seen:
            return
        seen.add(key)
        cases.append({
            "id": f"g{len(cases) + 1:03d}",
            "query": q,
            "type": type_,
            "expected_file_ids": expected_file_ids or [],
            "relevant_vector_ids": [],
            "source": source,
        })

    # 1. Real /query queries
    for (qt,) in db.execute(text("SELECT query_text FROM query ORDER BY created_at")):
        if qt:
            add(qt, "semantic", "query-log")

    # 2. Quotation user queries
    for (uq,) in db.execute(text("SELECT user_query FROM quotation WHERE user_query IS NOT NULL ORDER BY created_at")):
        if uq:
            add(uq, "semantic", "quotation-log")

    # 3. Lexical adversarial: exact document-name lookups with automatic ground truth.
    #    Dense-only retrieval typically fails these; hybrid search must put them top-3.
    lexical_sql = text("""
        SELECT id, original_filename, category FROM file_metadata
        WHERE processing_status = 'completed' AND chunk_count > 0
          AND (original_filename ILIKE 'COT-IMPAG-%' OR category = 'factura')
        ORDER BY random() LIMIT 12
    """)
    for fid, fname, category in db.execute(lexical_sql):
        stem = os.path.splitext(fname)[0]
        add(stem, "lexical", f"filename:{category}", expected_file_ids=[fid])

    db.close()

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)

    n_sem = sum(1 for c in cases if c["type"] == "semantic")
    n_lex = len(cases) - n_sem
    print(f"Golden set: {len(cases)} cases ({n_sem} semantic, {n_lex} lexical) -> {OUT_PATH}")
    print("Next: python scripts/rag_eval.py --annotate   (labels semantic cases with Claude)")


if __name__ == "__main__":
    main()
