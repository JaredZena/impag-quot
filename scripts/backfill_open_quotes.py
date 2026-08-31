#!/usr/bin/env python3
"""Backfill the verified open-quote book (ago 2026) into the quote table.

One-shot, idempotent: inserts the 11 open quotes verified against their PDFs
so the follow-up sweep (services/quote_followup.py) and the admin QuotesPage
have the real pipeline to work with. Quotes were flat totals with IVA
unspecified, so subtotal=total and iva_amount=0 — each row's notes flag
"IVA por definir en re-cotización".

Idempotent: a quote_number that already exists is skipped (and reported), so
re-running after a partial commit is safe. The pinned customer_ids are
verified with a read-only SELECT (name-token match against display_name)
before linking; a mismatch leaves customer_id NULL.

Usage:
    python scripts/backfill_open_quotes.py            # dry-run: list inserts/skips
    python scripts/backfill_open_quotes.py --commit   # write
"""

import os
import re
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Customer, Quote, SessionLocal

CREATED_BY = "backfill-cotizaciones-2026-08"
VALIDITY_DAYS = 3
IVA_NOTE = "IVA por definir en re-cotización."
NO_PHONE = "S/N"

# Verified against the quote PDFs (folio = quote_number, all <= 20 chars).
# date = original quote date -> sent_at at 12:00 UTC. phone = E.164 or None.
OPEN_QUOTES = [
    {
        "quote_number": "COT-IMPAG-040726DGO",
        "customer_name": "Jonathan Enns",
        "phone": None,
        "location": "Nuevo Ideal, Durango",
        "total": Decimal("122295.00"),
        "date": (2026, 7, 10),
        "context": (
            "Suministro e instalación plástico invernadero 480 m2 (túnel 10x48). "
            "Vía JD/Daniel — sin teléfono directo."
        ),
    },
    {
        "quote_number": "COT-IMPAG-220626NAY",
        "customer_name": "Comité Estatal Fomento y Protección Pecuaria de Nayarit",
        "phone": None,
        "location": "Plomeros 55, Cd Industrial, Tepic, Nayarit",
        "total": Decimal("75200.00"),
        "date": (2026, 6, 20),
        "context": (
            "400 paquetes trampa individual azul (20 pzas c/u). "
            "Entrega Tepic incluida. CFDI requerido."
        ),
    },
    {
        "quote_number": "COT-IMPAG-020726COL",
        "customer_name": "Comité Protección Pecuaria de Colima (Lesly)",
        "phone": "+524531086866",
        "location": "Colima",
        "total": Decimal("25000.00"),
        "date": (2026, 7, 2),
        "context": (
            "2,000 trampas individuales @ $12.50. Ofrecer precio volumen $9.40/pza."
        ),
    },
    {
        "quote_number": "COT-IMPAG-030726COL",
        "customer_name": "Comité Protección Pecuaria de Colima (Lesly)",
        "phone": "+524531086866",
        "location": "Colima",
        "total": Decimal("4600.00"),
        "date": (2026, 7, 4),
        "context": "1 galón 10L adhesivo/pegamento atrayente (POPUSA).",
    },
    {
        "quote_number": "COT-IMPAG-280626DGO",
        "customer_name": "Uriel",
        "phone": "+526771130211",
        "location": "Canatlán, Durango",
        "total": Decimal("86400.00"),
        "date": (2026, 6, 25),
        "customer_id": 158,
        "context": (
            "12 rollos malla antigranizo 3.7x200m 58g (1 ha). "
            "Descuento autorizado hasta ~$82k (margen 15→10%)."
        ),
    },
    {
        "quote_number": "COT-IMPAG-130626DGO",
        "customer_name": "Edwin Santillano",
        "phone": "+526182224568",
        "location": "Santiago Papasquiaro, Durango",
        "total": Decimal("72000.00"),
        "date": (2026, 6, 5),
        "customer_id": 284,
        "context": (
            "3 geomembranas 10,000L + kits + cerco. Técnico SV, cierra rápido; "
            "opción arrancar 1 bolsa ~$20.7k."
        ),
    },
    {
        "quote_number": "COT-IMPAG-010726DGO",
        "customer_name": "Abel Carrillo",
        "phone": "+526711012990",
        "location": "San Luis del Cordero, Durango",
        "total": Decimal("51840.00"),
        "date": (2026, 7, 2),
        "customer_id": 422,
        "context": (
            "Muestrario mallasombra 35/50/70/80/90% (1 rollo c/u). "
            "Ayudar a elegir densidad; meta 1 rollo."
        ),
    },
    {
        "quote_number": "COT-IMPAG-030626DGO",
        "customer_name": "Mary (CAC Duraznos)",
        "phone": "+526182703340",
        "location": "Nombre de Dios, Durango",
        "total": Decimal("35650.00"),
        "date": (2026, 6, 1),
        "customer_id": 324,
        "context": (
            "Material vivero SV — slice de wish-list total $129,669 (mayo). "
            "IMPAG resuelve facturas SV."
        ),
    },
    {
        "quote_number": "COT-IMPAG-290626PUE",
        "customer_name": "World Vision México",
        "phone": None,
        "location": "Santa Cruz San Antonio Mihuacán, Puebla",
        "total": Decimal("27500.00"),
        "date": (2026, 6, 25),
        "context": (
            "Mano de obra instalación plástico invernadero 150m2 + estanque peces. "
            "Contacto Mercedez Álvarez vía JD. Cliente paga 50/50 Coupa."
        ),
    },
    {
        "quote_number": "COT-IMPAG-300626PUE",
        "customer_name": "World Vision México",
        "phone": None,
        "location": "Santa Cruz San Antonio Mihuacán, Puebla",
        "total": Decimal("7100.00"),
        "date": (2026, 6, 25),
        "context": (
            "Accesorios instalación (malacates, cable, poleas, tornillería) — "
            "complemento de 290626PUE."
        ),
    },
    {
        "quote_number": "COT-IMPAG-120626DGO",
        "customer_name": "CONAPESCA (Diana Moreno / Beatriz García)",
        "phone": "+526185677880",
        "location": "Durango (programa Desarrollo Rural)",
        "total": Decimal("29200.00"),
        "date": (2026, 6, 5),
        "context": (
            "Estanque geomembrana 20,000L instalado — PRECIO UNITARIO de programa "
            "multi-unidad. Deben cotización actualizada desde 12-jun; mandar "
            "precios escalonados 5/10/20."
        ),
    },
]


def _name_tokens(name):
    """Lowercased tokens (>=3 chars) for a tolerant name-overlap check."""
    return {
        t for t in re.findall(r"[a-z0-9áéíóúüñ]+", (name or "").lower()) if len(t) >= 3
    }


def verify_customer_links(db):
    """Read-only check of the pinned customer_ids; returns the verified id set.

    A pinned id links only when the customer row exists AND shares at least one
    name token with the quote's customer_name — otherwise customer_id stays NULL.
    """
    pinned = {q["customer_id"]: q for q in OPEN_QUOTES if q.get("customer_id")}
    found = {
        c.id: c for c in db.query(Customer).filter(Customer.id.in_(pinned.keys())).all()
    }
    verified = set()
    print("Verificación customer_id (SELECT read-only sobre customer):")
    for cid, q in sorted(pinned.items()):
        cust = found.get(cid)
        if cust is None:
            print(
                f"  id {cid}: NO EXISTE — {q['quote_number']} se inserta con "
                f"customer_id NULL"
            )
            continue
        ok = bool(_name_tokens(q["customer_name"]) & _name_tokens(cust.display_name))
        verdict = "MATCH -> se vincula" if ok else "MISMATCH -> customer_id NULL"
        print(
            f"  id {cid}: cotización '{q['customer_name']}' vs BD "
            f"'{cust.display_name}' — {verdict}"
        )
        if ok:
            verified.add(cid)
    if not pinned:
        print("  (ninguna cotización trae customer_id)")
    return verified


def main():
    commit = "--commit" in sys.argv
    db = SessionLocal()
    try:
        verified_ids = verify_customer_links(db)

        existing = {
            qn
            for (qn,) in db.query(Quote.quote_number)
            .filter(Quote.quote_number.in_([q["quote_number"] for q in OPEN_QUOTES]))
            .all()
        }

        inserted = skipped = 0
        inserted_total = Decimal(0)
        print(
            f"\n{'COMMIT' if commit else 'DRY-RUN'} — {len(OPEN_QUOTES)} cotizaciones en el libro:"
        )
        for q in OPEN_QUOTES:
            if q["quote_number"] in existing:
                skipped += 1
                print(f"  SKIP   {q['quote_number']}  ya existe en quote — no se toca")
                continue

            cid = q.get("customer_id")
            cid = cid if cid in verified_ids else None
            y, m, d = q["date"]
            sent_at = datetime(y, m, d, 12, 0, 0, tzinfo=timezone.utc)
            row = Quote(
                quote_number=q["quote_number"],
                # Lifecycle in models.Quote: draft, sent, viewed, accepted,
                # rejected, expired — these are open quotes already delivered.
                status="sent",
                customer_name=q["customer_name"],
                customer_phone=q["phone"] or NO_PHONE,
                customer_location=q["location"],
                customer_id=cid,
                notes=f"{q['context']}\n{IVA_NOTE}",
                validity_days=VALIDITY_DAYS,
                # Flat totals on the PDFs; IVA unspecified -> see IVA_NOTE.
                subtotal=q["total"],
                iva_amount=Decimal("0.00"),
                total=q["total"],
                sent_at=sent_at,
                created_by=CREATED_BY,
                # Same shape the /send endpoint generates (uuid4 on send).
                access_token=str(uuid.uuid4()),
            )
            if commit:
                db.add(row)
            inserted += 1
            inserted_total += q["total"]
            print(
                f"  INSERT {q['quote_number']}  {q['customer_name'][:38]:38s} "
                f"${q['total']:>10,.2f}  sent {sent_at:%Y-%m-%d}  "
                f"tel {q['phone'] or NO_PHONE:14s} cid={cid if cid else '—'}"
            )

        print(
            f"\nTotal: {inserted} insert / {skipped} skip — "
            f"${inserted_total:,.2f} en cotizaciones abiertas"
        )
        if commit:
            db.commit()
            print("COMMIT hecho.")
        else:
            db.rollback()
            print("Dry-run — nada escrito. Re-ejecuta con --commit.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
