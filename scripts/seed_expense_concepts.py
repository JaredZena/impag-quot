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

from models import ExpenseConcept, SessionLocal  # noqa: E402

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
        "Impuestos SAT (mensual)",
        "operativo",
        "5427",
        "promedio pagos BNET Impuestos, periodos nov 2025–may 2026 (chat IMPAG contabilidad)",
    ),
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
