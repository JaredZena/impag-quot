"""Seed the Punto de equilibrio expense template with the monthly fixed costs
Juan Daniel confirmed on 2026-09-26 (WhatsApp + recibos de luz/agua/internet)
plus SAT payments from the BBVA statements in the IMPAG contabilidad chat.

Idempotent: concepts that already exist (by name) are left untouched, so
amounts edited later in the admin are never overwritten.

Run: venv/bin/python scripts/seed_expense_concepts.py [--dry-run]
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date  # noqa: E402

from models import ExpenseConcept, SessionLocal, TaxDeclaration  # noqa: E402

CONCEPTS = [
    ("Sueldo Hernán", "operativo", "10000", "JD 2026-09-26"),
    ("Renta", "operativo", "3000", "JD 2026-09-26"),
    ("Contadora", "operativo", "1200", "JD 2026-09-26"),
    ("Sueldo Adriana", "operativo", "1200", "JD 2026-09-26; jul/ago/sep sin pagar"),
    (
        "Internet",
        "operativo",
        "349",
        "promedio recibos ene–ago 2026 (abr no se pagó, may doble)",
    ),
    (
        "Agua",
        "operativo",
        "185.75",
        "promedio recibos ene–ago 2026 (feb y abr no pagados)",
    ),
    ("Luz", "operativo", "174.50", "CFE bimestral: $349 promedio por bimestre ÷ 2"),
    ("Recargas teléfono", "operativo", "150", "JD 2026-09-26"),
    (
        "Provisión declaración anual",
        "operativo",
        "1812",
        "anual 2025 = $21,740 (pagada 31/03/26) ÷ 12",
    ),
    (
        "Comisiones bancarias",
        "operativo",
        "70",
        "BBVA serv. banca internet + IVA, promedio dic 2025–jun 2026 ($22–$113)",
    ),
    ("Pago camioneta", "financiamiento", "14000", "JD 2026-09-26"),
]


# Total a pagar per periodo from the accountant's acuses in the "IMPAG
# contabilidad" chat (Hoja 2 of each; format-A main + format-B retenciones).
# Dic 2025 has no acuse — taken from the BBVA statement (21/01/2026 payments).
# Abr 2026 = the 01/06 complementaria, which replaced the original filing.
TAXES = [
    (date(2025, 11, 1), "2627", "acuse 15/12/2025: 2,501 + 126 ret."),
    (date(2025, 12, 1), "2084", "sin acuse; pagos BBVA 21/01/2026 (1,958 + 126)"),
    (date(2026, 1, 1), "2819", "acuse 12/02/2026: 2,693 + 126 ret."),
    (date(2026, 2, 1), "5933", "acuse 09/03/2026: 5,807 + 126 ret."),
    (date(2026, 3, 1), "11967", "acuse 16/04/2026: 11,841 + 126 ret."),
    (date(2026, 4, 1), "2024", "complementaria 01/06/2026: 1,894 + 130 ret."),
    (date(2026, 5, 1), "8557", "acuses 06/06/2026: 8,244 + 313"),
    (date(2026, 6, 1), "37566", "acuses 09/07/2026: 37,314 + 252 ret."),
]


def main(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        existing = {c.name.lower() for c in db.query(ExpenseConcept)}
        added = 0
        for order, (name, category, amount, notes) in enumerate(CONCEPTS, start=1):
            if name.lower() in existing:
                print(f"= {name} (ya existe)")
                continue
            print(f"+ {name}: {category} ${amount}")
            db.add(
                ExpenseConcept(
                    name=name,
                    category=category,
                    default_amount=Decimal(amount),
                    sort_order=order,
                    notes=notes,
                )
            )
            added += 1
        have = {t.month for t in db.query(TaxDeclaration)}
        for month, amount, notes in TAXES:
            if month in have:
                print(f"= impuestos {month:%Y-%m} (ya existe)")
                continue
            print(f"+ impuestos {month:%Y-%m}: ${amount}")
            db.add(TaxDeclaration(month=month, amount=Decimal(amount), notes=notes))
        if dry_run:
            db.rollback()
            print(f"dry-run: {added} por agregar")
        else:
            db.commit()
            print(f"{added} conceptos agregados")
    finally:
        db.close()


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
