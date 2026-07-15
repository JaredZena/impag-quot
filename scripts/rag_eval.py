#!/usr/bin/env python3
"""
RAG retrieval evaluation harness. Measures the PRODUCTION retrieval path
(generate_embeddings -> pinecone_service.search_vectors) against the golden set.

Modes:
  --annotate   Label semantic cases: retrieve top-20 candidates per query,
               judge relevance with Claude (haiku), write relevant_vector_ids
               back into eval/golden_set.json. Also writes eval/annotation_review.md
               for human spot-checking. Run once (and after major corpus changes).

  --run [--label NAME]
               Run the eval: retrieve top-7 (production top_k) per case, compute
               recall@7 / MRR@7 (semantic) and hit@3 / hit@7 (lexical).
               Saves eval/results_<label>.json. If eval/results_baseline.json
               exists and label != baseline, prints a comparison.

First run:  python scripts/rag_eval.py --run --label baseline
DB access is READ-ONLY; Pinecone is queried, never written.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
GOLDEN_PATH = os.path.join(EVAL_DIR, "golden_set.json")

TOP_K_PROD = 7      # production top_k in search_vectors consumers
TOP_K_ANNOTATE = 20  # candidate pool for relevance labeling

VECTOR_ID_RE = re.compile(r"^doc-(\d+)-chunk-(\d+)$")


def _load_golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_golden(cases):
    with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)


MODE = os.getenv("EVAL_MODE", "hybrid_rerank")  # dense | dense_rerank | hybrid | hybrid_rerank

# EVAL_UNIVERSE selects which production caller's namespace set to mirror:
#   files (default) — /files/search: all CATEGORY_TO_NAMESPACE namespaces
#   query           — the /query quotation path (_search_all_namespaces list,
#                     including the legacy '' namespace)
UNIVERSE = os.getenv("EVAL_UNIVERSE", "files")
QUERY_PATH_NAMESPACES = ['', 'cotizaciones', 'whatsapp-chats', 'catalogos', 'facturas', 'notas']


def _namespaces():
    return QUERY_PATH_NAMESPACES if UNIVERSE == "query" else None


def _retrieve(query: str, top_k: int):
    """Mirror a production retrieval path, selected via EVAL_MODE/EVAL_UNIVERSE."""
    from rag_system_moved.embeddings import generate_embeddings
    emb = generate_embeddings([query])[0]
    if MODE.startswith("hybrid"):
        from services.hybrid_search import hybrid_search
        return hybrid_search(query, emb, top_k=top_k, namespaces=_namespaces(),
                             rerank=MODE == "hybrid_rerank")
    from services.pinecone_service import search_vectors
    return search_vectors(emb, top_k=top_k, namespaces=_namespaces(),
                          query_text=query if MODE == "dense_rerank" else None)


def _hydrate_chunk_texts(vector_ids):
    """Full chunk text from Postgres (Pinecone metadata is truncated to 1000 chars)."""
    from sqlalchemy import text as sqltext
    from models import SessionLocal
    db = SessionLocal()
    db.execute(sqltext("SET default_transaction_read_only = on"))
    rows = db.execute(
        sqltext("SELECT pinecone_vector_id, chunk_text FROM document_chunk WHERE pinecone_vector_id = ANY(:ids)"),
        {"ids": list(vector_ids)},
    ).fetchall()
    db.close()
    return {r[0]: r[1] for r in rows}


def annotate():
    import anthropic
    from config import claude_api_key
    client = anthropic.Anthropic(api_key=claude_api_key)

    cases = _load_golden()
    semantic = [c for c in cases if c["type"] == "semantic"]
    review_lines = ["# Golden set annotation review", "",
                    "Claude-judged relevance labels. Spot-check ~15: fix any wrong label",
                    "directly in eval/golden_set.json (relevant_vector_ids), then re-run --run.", ""]

    for i, case in enumerate(semantic, 1):
        # Judge the UNION of dense and hybrid candidate pools so labels are
        # fair to every retrieval mode (labels from a single mode's pool bias
        # recall against the others).
        from rag_system_moved.embeddings import generate_embeddings
        from services.pinecone_service import search_vectors
        from services.hybrid_search import hybrid_search
        emb = generate_embeddings([case["query"]])[0]
        pool = search_vectors(emb, top_k=TOP_K_ANNOTATE)
        seen_ids = {r["id"] for r in pool}
        for r in hybrid_search(case["query"], emb, top_k=TOP_K_ANNOTATE, rerank=False):
            if r["id"] not in seen_ids:
                pool.append(r)
                seen_ids.add(r["id"])
        results = pool
        if not results:
            print(f"[{i}/{len(semantic)}] {case['id']}: no results")
            continue
        texts = _hydrate_chunk_texts([r["id"] for r in results])
        numbered = []
        for j, r in enumerate(results):
            body = texts.get(r["id"]) or r["metadata"].get("chunk_text", "")
            numbered.append(f"[{j}] (ns={r['namespace']}) {body[:600]}")

        prompt = (
            "Eres un evaluador de recuperación de información para IMPAG, una empresa de "
            "suministros agrícolas en México (riego, invernaderos, acolchados, geomembranas).\n\n"
            f"CONSULTA DEL USUARIO:\n{case['query']}\n\n"
            "FRAGMENTOS RECUPERADOS:\n" + "\n\n".join(numbered) + "\n\n"
            "¿Cuáles fragmentos son RELEVANTES para responder la consulta (contienen productos, "
            "precios, especificaciones o contexto que un vendedor usaría para cotizar/responder)? "
            'Responde SOLO JSON: {"relevant": [índices]}'
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        try:
            idxs = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))["relevant"]
        except Exception:
            print(f"[{i}/{len(semantic)}] {case['id']}: judge parse error: {raw[:80]}")
            continue
        case["relevant_vector_ids"] = [results[j]["id"] for j in idxs if 0 <= j < len(results)]
        print(f"[{i}/{len(semantic)}] {case['id']}: {len(case['relevant_vector_ids'])}/{len(results)} relevant")

        review_lines += [f"## {case['id']}: {case['query'][:120]}", ""]
        for j, r in enumerate(results):
            mark = "✅" if r["id"] in case["relevant_vector_ids"] else "—"
            body = (texts.get(r["id"]) or r["metadata"].get("chunk_text", ""))[:180].replace("\n", " ")
            review_lines.append(f"- {mark} `{r['id']}` {body}")
        review_lines.append("")

    _save_golden(cases)
    review_path = os.path.join(EVAL_DIR, "annotation_review.md")
    with open(review_path, "w", encoding="utf-8") as f:
        f.write("\n".join(review_lines))
    labeled = sum(1 for c in semantic if c["relevant_vector_ids"])
    print(f"\nAnnotated {labeled}/{len(semantic)} semantic cases. Review: {review_path}")


def run(label: str):
    cases = _load_golden()
    per_case = []
    for case in cases:
        results = _retrieve(case["query"], TOP_K_PROD)
        got_ids = [r["id"] for r in results]

        if case["type"] == "lexical":
            expected = set(case["expected_file_ids"])
            def file_of(vid):
                m = VECTOR_ID_RE.match(vid)
                return int(m.group(1)) if m else None
            ranks = [k for k, vid in enumerate(got_ids, 1) if file_of(vid) in expected]
            per_case.append({"id": case["id"], "type": "lexical", "query": case["query"][:80],
                             "hit@3": bool(ranks and ranks[0] <= 3), "hit@7": bool(ranks),
                             "first_rank": ranks[0] if ranks else None})
        else:
            relevant = set(case["relevant_vector_ids"])
            if not relevant:
                continue  # unlabeled — skipped, counted below
            hits = [k for k, vid in enumerate(got_ids, 1) if vid in relevant]
            recall = len(hits) / len(relevant)
            mrr = 1.0 / hits[0] if hits else 0.0
            per_case.append({"id": case["id"], "type": "semantic", "query": case["query"][:80],
                             "recall@7": round(recall, 3), "mrr@7": round(mrr, 3)})

    sem = [c for c in per_case if c["type"] == "semantic"]
    lex = [c for c in per_case if c["type"] == "lexical"]
    unlabeled = sum(1 for c in cases if c["type"] == "semantic" and not c["relevant_vector_ids"])
    summary = {
        "label": label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "semantic_cases": len(sem),
        "semantic_unlabeled_skipped": unlabeled,
        "recall@7": round(sum(c["recall@7"] for c in sem) / len(sem), 3) if sem else None,
        "mrr@7": round(sum(c["mrr@7"] for c in sem) / len(sem), 3) if sem else None,
        "lexical_cases": len(lex),
        "lexical_hit@3": round(sum(c["hit@3"] for c in lex) / len(lex), 3) if lex else None,
        "lexical_hit@7": round(sum(c["hit@7"] for c in lex) / len(lex), 3) if lex else None,
    }

    out = os.path.join(EVAL_DIR, f"results_{label}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "cases": per_case}, f, ensure_ascii=False, indent=1)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Saved: {out}")

    baseline_path = os.path.join(EVAL_DIR, "results_baseline.json")
    if label != "baseline" and os.path.exists(baseline_path):
        with open(baseline_path, encoding="utf-8") as f:
            base = json.load(f)["summary"]
        print("\nvs baseline:")
        for k in ("recall@7", "mrr@7", "lexical_hit@3", "lexical_hit@7"):
            b, n = base.get(k), summary.get(k)
            if b is not None and n is not None:
                arrow = "▲" if n > b else ("▼" if n < b else "=")
                print(f"  {k}: {b} -> {n} {arrow}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--annotate", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--label", default="baseline")
    args = ap.parse_args()
    if args.annotate:
        annotate()
    elif args.run:
        run(args.label)
    else:
        ap.print_help()
