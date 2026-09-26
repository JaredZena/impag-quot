"""Hermetic parse tests for services/supplier_price_sync (no DB, no network)."""

import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

from decimal import Decimal

from services.supplier_price_sync import COTIZADOR_TAB, parse_workbook

TABS = {
    COTIZADOR_TAB: [
        [],
        ["Dólar Compra"],
        [
            19.8,
            "",
            "",
            "Material ",
            "Descripcion ",
            "Cantidad ",
            "Unidad",
            "Precio U POPUSA",
            "Precio U ICUSA Plasticos",
            "Paqueteria Texcoco/Local",
            "Paqueteria Texcoco/TG Dgo",
            "TG Dgo/Ditra",
            "DITRA/Local",
        ],
        [
            "",
            "",
            "",
            "ACOLCHADO B/N 1.0M",
            "Ancho: 1.0m",
            1,
            "Rollo",
            1143,
            1110.9,
            453.56,
            383.96,
            150,
            313,
        ],
    ],
    "POPUSA": [
        [
            "",
            "Ultima Actualizacion",
            "Material",
            "Descripcion",
            "Cantidad",
            "Unidad",
            "Precio U",
            "Importe",
        ],
        ["", "11/09/2026", "ACOLCHADO", "Ancho: 1.0m", 1, "ROLLO", 1143, 1143],
        ["", "11/09/2026", "RAFIA", "sin precio", 1, "ROLLO", "", ""],
    ],
    "ICUSA Plasticos": [
        ["Dólar Compra"],
        [
            21,
            "",
            "",
            "Material",
            "Descripcion",
            "Cantidad",
            "Unidad",
            "Precio U",
            "Importe",
        ],
        [],
        ["", "", "", "ACOLCHADO", "Ancho: 1.0m", 116, "Rollo", "$ 1,110.90", 128864.4],
    ],
}
FORMULAS = [
    [],
    [],
    [],
    [
        "",
        "",
        "",
        "ACOLCHADO B/N 1.0M",
        "Ancho: 1.0m",
        1,
        "Rollo",
        "=+POPUSA!G2",
        "=+'ICUSA Plasticos'!H4",
    ],
]


def test_parses_rows_and_follows_cotizador_formulas():
    rows, skipped = parse_workbook(TABS, FORMULAS)
    by_tab = {r.tab: r for r in rows}
    assert set(by_tab) == {"POPUSA", "ICUSA Plasticos"}
    icusa = by_tab["ICUSA Plasticos"]
    assert icusa.row == 4 and icusa.cost == Decimal("1110.90")
    assert icusa.display_name == "ACOLCHADO B/N 1.0M"
    assert icusa.shipping["stage1"] + icusa.shipping["stage2"] + icusa.shipping[
        "stage3"
    ] == Decimal("846.96")
    assert by_tab["POPUSA"].updated_label == "11/09/2026"
    assert any("RAFIA" in s for s in skipped)


def test_key_is_stable_and_row_independent():
    rows, _ = parse_workbook(TABS, FORMULAS)
    shifted = {**TABS, "POPUSA": [TABS["POPUSA"][0], []] + TABS["POPUSA"][1:]}
    rows2, _ = parse_workbook(shifted, FORMULAS)
    k1 = {r.tab: r.key for r in rows}
    k2 = {r.tab: r.key for r in rows2}
    assert k1 == k2
