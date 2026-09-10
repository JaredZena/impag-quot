"""
Hermetic tests for the tools inventory (routes/tools.py) and the HERRAMIENTAS
sheet parser (scripts/import_tools_from_sheet.py). SQLite in-memory with only
the tool tables, R2 mocked — no network, no real DB.

Run: venv/bin/python -m pytest tests/test_tools.py -q
"""

import os

# Must run BEFORE any project import (same guard as tests/test_campaigns.py):
# the fake DATABASE_URL guarantees models.py's module-level engine can never
# reach production, and ALEMBIC_RUNNING=1 skips its create_all.
os.environ["DATABASE_URL"] = "postgresql://test:test@tools-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

import io
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.tools as tools_routes
from models import Base, Tool, ToolMovement, get_db
from scripts.import_tools_from_sheet import parse_rows

TABLES = [Tool.__table__, ToolMovement.__table__]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)

    app = FastAPI()
    app.include_router(tools_routes.router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    uploads: dict = {}
    deleted: list = []
    monkeypatch.setattr(
        tools_routes,
        "r2_upload",
        lambda key, data, content_type, bucket=None: uploads.__setitem__(
            key, (content_type, bucket)
        ),
    )
    monkeypatch.setattr(
        tools_routes,
        "r2_delete",
        lambda key, bucket=None: deleted.append((key, bucket)),
    )
    monkeypatch.setattr(
        tools_routes,
        "generate_presigned_view_url",
        lambda key, content_type, ttl, bucket=None: f"https://signed.test/{key}",
    )
    c = TestClient(app)
    c.uploads, c.deleted = uploads, deleted
    return c


def _create(client, **overrides):
    body = {
        "name": "Llave caimán 6 pulgadas",
        "quantity": 1,
        "unit_cost": 3068.32,
        "supplier_name": "TORNILLO",
    }
    body.update(overrides)
    resp = client.post("/tools", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _move(client, tool_id, **body):
    return client.post(f"/tools/{tool_id}/movements", json=body)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (40, 30), (255, 0, 0, 128)).save(buf, format="PNG")
    return buf.getvalue()


def test_create_defaults_and_logs_compra(client):
    tool = _create(client)
    assert tool["status"] == "en_local"
    assert tool["kind"] == "herramienta"
    assert tool["unit"] == "PIEZA"
    assert tool["quantity"] == 1.0
    assert tool["total_value"] == 3068.32
    assert tool["holder"] is None
    assert [m["kind"] for m in tool["movements"]] == ["compra"]
    assert tool["movements"][0]["to_status"] == "en_local"


def test_create_validation(client):
    assert client.post("/tools", json={"name": "   "}).status_code == 400
    assert (
        client.post("/tools", json={"name": "Pala", "quantity": 0}).status_code == 400
    )
    assert (
        client.post("/tools", json={"name": "Pala", "unit_cost": -1}).status_code == 400
    )
    assert (
        client.post("/tools", json={"name": "Pala", "kind": "vehiculo"}).status_code
        == 400
    )
    assert (
        client.post("/tools", json={"name": "Pala", "status": "baja"}).status_code
        == 400
    )
    assert client.post("/tools", json={"quantity": 1}).status_code == 422
    # Registered straight into an install: the holder is mandatory.
    assert (
        client.post("/tools", json={"name": "Pala", "status": "en_obra"}).status_code
        == 400
    )
    out = _create(client, name="Pala", status="en_obra", holder="Obra 15 HP Rodeo")
    assert out["holder"] == "Obra 15 HP Rodeo"
    assert out["movements"][0]["holder"] == "Obra 15 HP Rodeo"
    # A tool in the store never keeps a holder.
    local = _create(client, name="Nivel", holder="Chubeto")
    assert local["holder"] is None


def test_lifecycle_movements(client):
    tool = _create(client, status="pendiente_entrega", holder="Jared")
    tid = tool["id"]
    assert tool["holder"] == "Jared"  # optional context while pendiente

    assert _move(client, tid, to_status="pendiente_entrega").status_code == 400

    resp = _move(client, tid, to_status="en_local")
    assert resp.status_code == 200, resp.text
    tool = resp.json()["data"]
    assert tool["status"] == "en_local" and tool["holder"] is None
    assert tool["movements"][0]["kind"] == "recibida"

    assert _move(client, tid, to_status="en_obra").status_code == 400  # holder required
    tool = _move(
        client,
        tid,
        to_status="en_obra",
        holder="Obra Silerio",
        note="Instalación bombeo",
    ).json()["data"]
    assert tool["status"] == "en_obra" and tool["holder"] == "Obra Silerio"
    assert tool["movements"][0]["kind"] == "salida"
    assert tool["movements"][0]["holder"] == "Obra Silerio"

    tool = _move(client, tid, to_status="en_local", location="Texcoco").json()["data"]
    assert tool["movements"][0]["kind"] == "regreso"
    assert tool["holder"] is None and tool["location"] == "Texcoco"

    assert _move(client, tid, to_status="baja").status_code == 400  # reason required
    tool = _move(
        client,
        tid,
        to_status="baja",
        note="Se rompió en la obra",
        occurred_on="2026-09-01",
    ).json()["data"]
    assert tool["status"] == "baja"
    assert tool["retired_reason"] == "Se rompió en la obra"
    assert tool["retired_at"] is not None
    assert tool["movements"][0]["occurred_on"] == "2026-09-01"

    tool = _move(client, tid, to_status="en_local", note="Se reparó").json()["data"]
    assert tool["movements"][0]["kind"] == "reactivar"
    assert tool["retired_at"] is None and tool["retired_reason"] is None
    assert [m["kind"] for m in tool["movements"]] == [
        "reactivar",
        "baja",
        "regreso",
        "salida",
        "recibida",
        "compra",
    ]
    assert _move(client, tid, to_status="perdida").status_code == 400
    assert _move(client, 999, to_status="en_local").status_code == 404


def test_update_fields_and_quantity_adjustment(client):
    tid = _create(client, quantity=1)["id"]
    resp = client.put(
        f"/tools/{tid}",
        json={"quantity": 3, "notes": "  Falta el estuche  ", "status": "baja"},
    )
    assert resp.status_code == 200, resp.text
    tool = resp.json()["data"]
    assert tool["quantity"] == 3.0
    assert tool["status"] == "en_local"  # status is NOT editable via PUT
    assert tool["notes"] == "Falta el estuche"
    assert tool["movements"][0]["kind"] == "ajuste"
    assert tool["movements"][0]["note"] == "Cantidad 1 → 3"

    # Unchanged quantity logs nothing; clearing the cost is allowed.
    tool = client.put(f"/tools/{tid}", json={"quantity": 3, "unit_cost": None}).json()[
        "data"
    ]
    assert len(tool["movements"]) == 2
    assert tool["unit_cost"] is None and tool["total_value"] is None

    assert client.put(f"/tools/{tid}", json={"quantity": -1}).status_code == 400
    assert client.put(f"/tools/{tid}", json={"name": ""}).status_code == 400
    # A holder only makes sense outside the store.
    assert client.put(f"/tools/{tid}", json={"holder": "Lencho"}).status_code == 400
    assert client.put(f"/tools/{tid}", json={"holder": None}).status_code == 200
    _move(client, tid, to_status="con_cliente", holder="Cliente A")
    tool = client.put(f"/tools/{tid}", json={"holder": "Cliente B"}).json()["data"]
    assert tool["holder"] == "Cliente B"
    assert client.put(f"/tools/{tid}", json={"holder": ""}).status_code == 400


def test_list_filters_search_and_summary(client):
    _create(client, name="Pala pico Truper", quantity=2, unit_cost=229)
    broca = _create(client, name="Broca 3/8", kind="consumible", unit_cost=None)
    poli = _create(client, name="Polipasto 1 tonelada", unit_cost=1782.99)
    _move(client, poli["id"], to_status="en_obra", holder="Chubeto")
    cuter = _create(client, name="Cúter 100% roto", unit_cost=39)
    _move(client, cuter["id"], to_status="baja", note="Roto")

    data = client.get("/tools").json()["data"]
    assert [t["name"] for t in data["items"]] == [
        "Broca 3/8",
        "Pala pico Truper",
        "Polipasto 1 tonelada",
    ]  # bajas hidden, case-insensitive name order
    summary = data["summary"]
    assert summary["active_count"] == 3
    assert summary["total_value"] == pytest.approx(2 * 229 + 1782.99)
    assert summary["missing_cost_count"] == 1
    assert summary["retired_count"] == 1
    assert summary["by_status"]["en_obra"] == {
        "label": "En obra",
        "count": 1,
        "value": 1782.99,
    }

    assert len(client.get("/tools?include_retired=true").json()["data"]["items"]) == 4
    assert [
        t["id"] for t in client.get("/tools?status=baja").json()["data"]["items"]
    ] == [cuter["id"]]
    assert [
        t["id"] for t in client.get("/tools?kind=consumible").json()["data"]["items"]
    ] == [broca["id"]]
    assert [
        t["id"] for t in client.get("/tools?q=chubeto").json()["data"]["items"]
    ] == [poli["id"]]
    assert (
        len(client.get("/tools?q=tornillo").json()["data"]["items"]) == 3
    )  # supplier match
    # LIKE metacharacters are literal: "100%" must not match everything.
    hits = client.get("/tools?q=100%25&include_retired=true").json()["data"]["items"]
    assert [t["id"] for t in hits] == [cuter["id"]]
    assert client.get("/tools?status=perdida").status_code == 400
    assert client.get("/tools/summary").json()["data"]["active_count"] == 3


def test_photos_upload_order_delete(client, monkeypatch):
    tid = _create(client)["id"]
    resp = client.post(
        f"/tools/{tid}/images", files={"file": ("foto.png", _png_bytes(), "image/png")}
    )
    assert resp.status_code == 200, resp.text
    key1 = resp.json()["data"]["key"]
    assert key1.startswith(f"tool-images/{tid}/") and key1.endswith(".webp")
    assert client.uploads[key1] == ("image/webp", None)  # private bucket = default
    key2 = client.post(
        f"/tools/{tid}/images", files={"file": ("b.png", _png_bytes(), "image/png")}
    ).json()["data"]["key"]

    bad = client.post(
        f"/tools/{tid}/images", files={"file": ("x.png", b"not an image", "image/png")}
    )
    assert bad.status_code == 400
    pdf = client.post(
        f"/tools/{tid}/images",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert pdf.status_code == 415
    monkeypatch.setattr(tools_routes, "MAX_IMAGES_PER_TOOL", 2)
    full = client.post(
        f"/tools/{tid}/images", files={"file": ("c.png", _png_bytes(), "image/png")}
    )
    assert full.status_code == 400

    tool = client.get(f"/tools/{tid}").json()["data"]
    assert [i["key"] for i in tool["images"]] == [key1, key2]
    assert tool["primary_image_url"] == f"https://signed.test/{key1}"

    assert (
        client.put(f"/tools/{tid}/images/order", json={"keys": [key2]}).status_code
        == 400
    )
    reordered = client.put(f"/tools/{tid}/images/order", json={"keys": [key2, key1]})
    assert reordered.json()["data"]["images"] == [key2, key1]

    missing = client.request(
        "DELETE", f"/tools/{tid}/images", json={"key": "tool-images/otra.webp"}
    )
    assert missing.status_code == 404
    assert (
        client.request("DELETE", f"/tools/{tid}/images", json={"key": key2}).status_code
        == 200
    )
    assert client.deleted == [(key2, None)]
    assert [i["key"] for i in client.get(f"/tools/{tid}").json()["data"]["images"]] == [
        key1
    ]


def test_delete_tool_removes_movements_and_photos(client):
    tid = _create(client)["id"]
    key = client.post(
        f"/tools/{tid}/images", files={"file": ("a.png", _png_bytes(), "image/png")}
    ).json()["data"]["key"]
    _move(client, tid, to_status="en_obra", holder="Obra")
    assert client.delete(f"/tools/{tid}").status_code == 200
    assert client.get(f"/tools/{tid}").status_code == 404
    assert (key, None) in client.deleted
    db = TestingSession()
    try:
        assert db.query(ToolMovement).count() == 0
    finally:
        db.close()


# ==================== HERRAMIENTAS sheet parser ====================


def _row(values, red=False):
    return [{"v": v, "bg": "#ff0000" if red else "#ffffff"} for v in values]


HEADER = [
    "",
    "",
    "",
    "CARACTER",
    "NO",
    "DESCRIPCION",
    "CANTIDAD",
    "UNIDAD",
    "PRECIO UNITARIO",
    "IMPORTE",
    "FECHA INICIO ACTUALIZACION",
    "ULTIMA ACTUALIZACION",
    "Estado ",
    "COMENTARIO ",
]
SHEET = [
    _row(["", "", "", "", "", "HERRAMIENTA EN EL LOCAL ", "", "", "CHUBETO"]),
    _row(["", "", "", "", "", "HERRAMIENTA FUERA DEL LOCAL", "$410.00"]),
    [],
    _row(["", "", "", "", " TOTAL", " $ 42,035.51 "]),
    _row(HEADER),
    _row(
        [
            "",
            "",
            "",
            "HERRAMIENTA",
            "1",
            "Arco p/segueta  sin segueta pretul",
            "2",
            "PIEZA",
            "$101.00",
            "$202.00",
            "5/9/2024",
            "19/08/2025",
            "1. Pendiente Entrega",
            "-",
        ],
        red=True,
    ),
    _row(
        [
            "",
            "1",
            "",
            "CONSUMIBLE",
            "6",
            "BROCA 5/16",
            "1",
            "PIEZA",
            "$74.01",
            "$74.01",
            "14/8/2025",
            "19/08/2025",
            "2. En el Local",
            "TORNILLO",
        ]
    ),
    _row(
        [
            "",
            "",
            "",
            "HERRAMIENTA",
            "4",
            "LLAVE MC4",
            "2",
            "PIEZA",
            "$601.00",
            "$601.00",
            "15/8/2025",
            "20/08/2025",
            "1. Pendiente Entrega",
        ]
    ),
    _row(
        [
            "",
            "",
            "",
            "HERRAMIENTA",
            "67",
            "PINZAS DE PRESION",
            "1",
            "PIEZA",
            "$350.00",
            "$350.00",
            "17/3/2026",
            "17/03/2026",
            "1. Pendiente Entrega",
            "JARED",
        ]
    ),
    _row(
        [
            "",
            "",
            "",
            "CONSUMIBLE",
            "85",
            "PEGAMENTO PVC 250 ML",
            "1",
            "PIEZA",
            "$169.00",
            "$169.00",
            "9/7/2026",
            "09/07/2026",
            "3. Con el Cliente",
            "TORNILLO",
        ]
    ),
    _row(
        [
            "",
            "",
            "",
            "HERRAMIENTA",
            "69",
            "MULTIMETRO TRUPER",
            "1",
            "PIEZA",
            "$155.00",
            "$155.00",
            "17/3/2026",
            "17/03/2026",
            "2. En el Local",
            "FERRETERIA ZALAS",
        ]
    ),
    _row(["", "", "", "", "", "", "", "", "", ""]),
    _row(["", "", "", "", "", "Nota suelta sin número"]),
]


def test_parse_sheet_rows():
    items, skipped = parse_rows(SHEET)
    by_no = {it["sheet_no"]: it for it in items}
    assert sorted(by_no) == [1, 4, 6, 67, 69, 85]

    arco = by_no[1]
    assert arco["name"] == "Arco p/segueta sin segueta pretul"  # whitespace collapsed
    assert arco["status"] == "pendiente_entrega" and arco["location"] is None
    assert arco["quantity"] == Decimal("2.00") and arco["unit_cost"] == Decimal(
        "101.00"
    )
    assert arco["purchase_date"].isoformat() == "2024-09-05"  # dd/mm (es-MX)
    assert arco["flags"]["fuera_del_local"] and "fuera del local" in arco["notes"]
    assert arco["supplier_name"] is None  # "-" is not a supplier nor a note

    broca = by_no[6]
    assert broca["kind"] == "consumible" and broca["supplier_name"] == "TORNILLO"
    assert broca["status"] == "en_local" and broca["location"] == "Nuevo Ideal"
    assert broca["notes"] is None

    assert by_no[4]["flags"]["importe_mismatch"] and "Revisar" in by_no[4]["notes"]
    assert by_no[67]["notes"] == "Comentario en la hoja: JARED"
    assert by_no[67]["supplier_name"] is None
    assert by_no[69]["supplier_name"] == "FERRETERIA ZALAS"
    assert (
        by_no[85]["status"] == "con_cliente" and by_no[85]["holder"] == "Por confirmar"
    )
    assert skipped == ["fila 13: sin NO numérico o sin descripción"]
