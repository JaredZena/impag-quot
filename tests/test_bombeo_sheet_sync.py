"""Hermetic parse tests for services/bombeo_sheet_sync (no DB, no network)."""

import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

from decimal import Decimal

from services.bombeo_sheet_sync import (
    COTIZADOR_TAB,
    KITS_TAB,
    PARTS_TAB,
    kit_price,
    parse_kits,
    parse_parts,
)

PART_HEADER = [
    "",
    "Fecha Creacion",
    "Producto",
    "Nombre",
    "Descripcion",
    "Cantidad",
    "Unidad",
    "Precio unitario USD",
    "Importe USD",
    "Precio unitario MXN",
    "Importe MXN",
]

PARTS = [
    [],
    ["", "Bombas Sumergibles solares"],
    ["", "", "", "", "", "Dólar", 18],
    PART_HEADER,
    [
        "",
        "",
        "KOLOS3-47-40-4",
        "Bomba",
        "400 W",
        1,
        "PZA",
        298.86,
        298.86,
        5379.52,
        5379.52,
    ],
    ["", "", "", "", "", "", "", "Total", 298.86, "", 5379.52],
    ["", "KIt Empate"],  # section title, no price: ignored silently
    PART_HEADER,
    ["", "", "CABLE3X8", "Cable", "3x8", 1, "M", 8.95, 8.95, 161.19, 161.19],
    ["", "", "KOLOS3-47-40-4", "Bomba", "dup", 1, "PZA", 1, 1, 1, 1],
]

BLOCK_HEADER = [
    "",
    "CODIGO",
    "NO",
    "DESCRIPCION",
    "CANTIDAD",
    "UNIDAD",
    "PRECIO UNITARIO",
    "IMPORTE",
]
# Two blocks: a plain subtotal (rows 3-7) and one with a "Precio De Venta"
# under it (rows 9-15), the shape that double-counted margin in the sheet.
KIT_VALUES = [
    [],
    ["", "", "", "KOLOS3-47-40-4"],
    BLOCK_HEADER,
    ["", "VDE SOLUCIONES SOLARES"],
    ["", "CABLE3X8", 20, "CABLE PLANO", 50, "M", 161.19, 8059.5],
    ["", "KOLOS3-47-40-4", 10, "SIST.SUM.KOLOS 47M", 1, "PZA", 5379.52, 5379.52],
    ["", "", "", "", "", "", "Subtotal", 13439.02],
    [],
    BLOCK_HEADER,
    ["", "KOLOS-AP550X-48", 10, "BOMBA SUPERFICIE", 1, "PZA", 3588.02, 3588.02],
    ["", "CABLE3X8", 20, "CABLE PLANO", 2, "M", 161.19, 322.38],
    ["", "", "", "", "", "", "Subtotal", 3910.4],
    ["", "", "", "", "", "", " % Ganancia", 0.25],
    ["", "", "", "", "", "", " Precio De Venta ", 5213.87],
]
KIT_FORMULAS = [[] for _ in KIT_VALUES]
KIT_FORMULAS[6] = ["", "", "", "", "", "", "Subtotal", "=+SUM(H4:H6)"]
KIT_FORMULAS[11] = ["", "", "", "", "", "", "Subtotal", "=+SUM(H10:H11)"]
KIT_FORMULAS[13] = ["", "", "", "", "", "", "", "=+H12/(1-H13)"]

COT_HEADER = [
    "",
    "Material ",
    "Descripcion ",
    " Cantidad ",
    "Unidad",
    "VDE",
    "",
    "",
    "",
    "Paqueteria Texcoco/Local",
    " PAQUETERIA OPCION 2",
    "Paqueteria Texcoco/TG Durang",
    "TG Dgo/Manzanita Dgo",
    "Manzanita Dgo/manzanita NI",
    "Manzanita NI/Local",
]
COT_VALUES = [
    [],
    COT_HEADER,
    [
        "",
        "Bombeo Solar",
        "Kit Bombeo Solar 0.5 HP-47M-40LPM",
        1,
        "Kit",
        13439.02,
        "",
        "",
        "",
        0,
        150,
        100,
        50,
        0,
        0,
    ],
    ["", "Bombeo Solar", "KOLOS-AP550X-48: 550 W", 1, "Kit", 5213.87],
    ["", "Bombeo Solar MP", "Kit Bombeo Solar 3 HP-110M-200LPM", 1, "Kit", ""],
    ["", "Material ", "Descripcion ", " Cantidad ", "Unidad", 0.18],
    ["", "Bombeo Solar", "Kit Bombeo Solar 0.5 HP-47M-40LPM", 1, "Kit", 16388.0],
]
COT_FORMULAS = [
    [],
    COT_HEADER,
    ["", "", "", "", "", "='Villarreal Division Equipos'!H7"],
    ["", "", "", "", "", "='Villarreal Division Equipos'!H14"],
    ["", "", "", "", "", ""],
    [],
    ["", "", "", "", "", "=R3"],
]

WORKBOOK = {
    "values": {PARTS_TAB: PARTS, KITS_TAB: KIT_VALUES, COTIZADOR_TAB: COT_VALUES},
    "formulas": {KITS_TAB: KIT_FORMULAS, COTIZADOR_TAB: COT_FORMULAS},
}


def test_parts_across_sections_first_code_wins():
    parts, skipped = parse_parts(PARTS)
    assert set(parts) == {"KOLOS3-47-40-4", "CABLE3X8"}
    assert parts["KOLOS3-47-40-4"].cost == Decimal("5379.52")
    assert parts["KOLOS3-47-40-4"].cost_usd == Decimal("298.86")
    assert any("repetido" in s for s in skipped)
    assert not any("Empate" in s for s in skipped)


def test_kit_cost_is_the_subtotal_never_the_sale_price():
    kits, skipped = parse_kits(WORKBOOK)
    by_pump = {k.pump_code: k for k in kits}
    assert set(by_pump) == {"KOLOS3-47-40-4", "KOLOS-AP550X-48"}
    # Cotizador points at "Precio De Venta" (+25%); the sync follows it back
    # to the =SUM subtotal so the 30% margin is not stacked on top.
    assert by_pump["KOLOS-AP550X-48"].cost == Decimal("3910.40")
    assert by_pump["KOLOS-AP550X-48"].block_cell == "H12"
    assert any("3 HP-110M" in s and "sin precio" in s for s in skipped)


def test_kit_identity_is_the_pump_not_the_first_line():
    kits, _ = parse_kits(WORKBOOK)
    kit = next(k for k in kits if k.name.startswith("Kit Bombeo Solar 0.5"))
    assert kit.lines[0].code == "CABLE3X8"
    assert kit.pump_code == "KOLOS3-47-40-4"
    assert kit.key == "gsheet:kit:KOLOS3-47-40-4"
    assert [(ln.code, ln.quantity) for ln in kit.lines] == [
        ("CABLE3X8", Decimal("50.00")),
        ("KOLOS3-47-40-4", Decimal("1.00")),
    ]
    assert kit.shipping["stage1"] == Decimal("100.00")
    assert kit.shipping["direct"] == Decimal("0")


def test_kit_price_is_thirty_percent_on_sale_price():
    assert kit_price(Decimal("7000")) == Decimal("10000.00")
    assert kit_price(Decimal("40521.98")) == Decimal("57888.54")
