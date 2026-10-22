#!/usr/bin/env python3
"""
Try an Agroproyectos idea against the real Claude API, locally, without ever
touching a real database.

Runs create → plan → productos → riego (when the plan says it needs one) →
one follow-up question, against a throwaway sqlite file, and prints each
step's duration and cost plus a readable summary. Candidates come from the
storefront's own catalog JSON (todoparaelcampo-storefront), so product picks
are checked against real handles and prices the same way the live endpoint
does.

SAFETY: refuses to run unless DATABASE_URL already points at sqlite — checked
BEFORE importing models/config, so a misconfigured .env (the repo's .env
points at the PRODUCTION Neon database) can never be touched by this script.

Usage:
    DATABASE_URL=sqlite:////tmp/agro-try.db GITHUB_STOREFRONT_TOKEN= \\
    RESEND_API_KEY= GMAIL_SMTP_USER= \\
    venv/bin/python scripts/agro_try_local.py [--idea "..."] [--json out.json] [--state durango]
"""

import argparse
import json
import os
import sys
import time

if not os.environ.get("DATABASE_URL", "").startswith("sqlite"):
    print(
        "Refusing to run: DATABASE_URL must start with 'sqlite' before this script "
        "imports anything. Example:\n"
        "  DATABASE_URL=sqlite:////tmp/agro-try.db GITHUB_STOREFRONT_TOKEN= "
        "RESEND_API_KEY= GMAIL_SMTP_USER= venv/bin/python scripts/agro_try_local.py",
        file=sys.stderr,
    )
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Safe now: DATABASE_URL is sqlite. models.py creates its own tables on import.
from models import Base, SessionLocal, engine
from routes.agroproyectos import AgroCreate
from services import agroproyectos, background

# plan/productos/riego hand their Claude call to services.background (App
# Runner's 120s request cap is well under a real call's wall-clock — see
# services/agroproyectos.py); run it inline here so this CLI blocks until
# each step is actually done, same as its own test suite does.
background.set_sync_for_tests(True)

DEFAULT_IDEA = (
    "Tengo un invernadero de 500 m² en Durango y quiero empezar a criar borregos de "
    "engorda; presupuesto 150 mil pesos"
)
DEFAULT_PRODUCTS_JSON = "/Users/jared/Documents/impag/todoparaelcampo-storefront-campo/src/data/products.json"
FOLLOW_UP_QUESTION = (
    "¿Qué raza me recomiendas empezar primero y por qué, dado mi presupuesto?"
)


class Candidate:
    """Duck-types routes.agroproyectos.Candidate (handle/title/category_slug/
    price_total/iva_rate/unit_label/product_id) without pydantic validation,
    since the storefront JSON doesn't carry every field the live endpoint's
    Candidate model requires."""

    def __init__(
        self,
        handle,
        title,
        category_slug,
        price,
        price_total,
        iva_rate,
        unit_label,
        product_id,
    ):
        self.handle = handle
        self.title = title
        self.category_slug = category_slug
        self.price = price
        self.price_total = price_total
        self.iva_rate = iva_rate
        self.unit_label = unit_label
        self.product_id = product_id


def load_candidates(path: str) -> list[Candidate]:
    if not os.path.isfile(path):
        print(
            f"products.json not found at {path}; continuing with 0 candidates.",
            file=sys.stderr,
        )
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    candidates = []
    for row in rows:
        price = row.get("price")
        price = float(price) if isinstance(price, (int, float)) and price > 0 else None
        price_total = round(price * 1.16, 2) if price is not None else None
        candidates.append(
            Candidate(
                handle=row.get("handle"),
                title=row.get("title"),
                category_slug=row.get("category_slug"),
                price=price,
                price_total=price_total,
                iva_rate=0.16,
                unit_label=None,
                product_id=row.get("backend_id"),
            )
        )
    return [c for c in candidates if c.handle and c.title]


def _timed(label: str, fn, *args, **kwargs):
    started = time.monotonic()
    result = fn(*args, **kwargs)
    elapsed = time.monotonic() - started
    print(f"  [{elapsed:6.1f}s] {label}")
    return result, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idea", default=DEFAULT_IDEA)
    parser.add_argument("--state", default="durango")
    parser.add_argument("--budget", type=float, default=150_000)
    parser.add_argument(
        "--area", type=float, default=500, help="Land size (see --unit)"
    )
    parser.add_argument("--unit", choices=["m2", "ha"], default="m2")
    parser.add_argument(
        "--json", dest="json_out", default=None, help="Dump the final AgroPublic here"
    )
    parser.add_argument("--products-json", default=DEFAULT_PRODUCTS_JSON)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--effort", default="medium")
    args = parser.parse_args()

    Base.metadata.create_all(
        bind=engine
    )  # defensive; models.py already did this on import
    candidates = load_candidates(args.products_json)
    print(f"Loaded {len(candidates)} catalog candidates from {args.products_json}")

    db = SessionLocal()
    try:
        settings = agroproyectos.read_settings(db)
        if args.model != settings.model or args.effort != settings.effort:
            settings = agroproyectos.write_settings(
                db, {"model": args.model, "effort": args.effort}, "agro_try_local"
            )
        print(f"Model: {settings.model} · effort: {settings.effort}")

        body = AgroCreate.model_validate(
            {
                "producer_type": "nuevo",
                "idea": args.idea,
                "current_operation": None,
                "state": args.state,
                "municipio": None,
                "land": {"has_land": True, "area": args.area, "unit": args.unit},
                "water": "pozo",
                "electricity": "cfe",
                "experience": "algo",
                "goal": "negocio",
                "timeline": "3-meses",
                "budget_mxn": args.budget,
                "budget_unknown": False,
                "wants_financing": False,
                "contact": {
                    "name": "Prueba Local",
                    "phone": "+526181234567",
                    "email": None,
                },
                "utm": None,
            }
        )

        created = agroproyectos.create_project(db, body, settings)
        project = agroproyectos.get_by_token(db, created["token"])
        print(f"\nProyecto {project.ref} ({project.token}) — idea: {args.idea!r}\n")

        total_elapsed = 0.0

        _, elapsed = _timed("plan", agroproyectos.run_plan, db, project, settings)
        # run_plan submits the Claude call to services.background, which
        # commits through ITS OWN db session (SessionLocal()) — same engine
        # as this script's `db`, but a different session, so this script's
        # `project` object needs an explicit refresh to see the result (sync
        # mode has already finished the job by the time run_plan returns).
        db.refresh(project)
        total_elapsed += elapsed
        plan = project.plan or {}
        print(
            f"    título: {plan.get('title')!r} · veredicto: {(plan.get('viability') or {}).get('verdict')}"
        )

        _, elapsed = _timed(
            "productos", agroproyectos.run_productos, db, project, settings, candidates
        )
        db.refresh(project)
        total_elapsed += elapsed
        print(
            f"    productos elegidos: {len(((project.products or {}).get('items')) or [])}"
        )

        if plan.get("needs_irrigation"):
            _, elapsed = _timed(
                "riego", agroproyectos.run_riego, db, project, settings, candidates
            )
            db.refresh(project)
            total_elapsed += elapsed
            print("    diseño de riego generado")
        else:
            print("  [  skip ] riego (el plan dice que no se necesita)")

        qa, elapsed = _timed(
            "pregunta de seguimiento",
            agroproyectos.ask_question,
            db,
            project,
            settings,
            FOLLOW_UP_QUESTION,
        )
        total_elapsed += elapsed
        print(f'\n  Pregunta: "{FOLLOW_UP_QUESTION}"')
        print(f"  Respuesta: {qa['answer']}\n")

        cost = float(project.cost_usd or 0)
        print(f"Costo total: ${cost:.4f} USD · tiempo total: {total_elapsed:.1f}s")

        public = agroproyectos.agro_public(db, project, settings)
        print("\n── Resumen ──────────────────────────────────────────")
        print(f"Veredicto: {(plan.get('viability') or {}).get('verdict')}")
        print(f"Explicación: {(plan.get('viability') or {}).get('explanation')}")
        budget = plan.get("budget") or {}
        print(
            f"Inversión: ${budget.get('investment_min_mxn', 0):,.0f}"
            f"–${budget.get('investment_max_mxn', 0):,.0f} MXN "
            f"(arranque recomendado ${budget.get('recommended_start_mxn', 0):,.0f})"
        )
        for kn in plan.get("key_numbers") or []:
            print(f"  · {kn.get('label')}: {kn.get('value')}")
        for opt in plan.get("options") or []:
            flag = "★" if opt.get("recommended") else " "
            print(f"  [{flag}] {opt.get('name')} ({opt.get('kind')}): {opt.get('why')}")
        print("────────────────────────────────────────────────────\n")

        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(public, fh, ensure_ascii=False, indent=1)
            print(f"AgroPublic dumped to {args.json_out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
