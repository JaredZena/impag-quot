"""
Hermetic tests for the quote PDF (services/quote_pdf.py): parsing the team's
COT-IMPAG PDF, POST /quotes/capture-pdf, and attaching / listing a quote's
PDFs. SQLite in-memory; R2, PDF text extraction and RAG indexing are stubbed.

Run: venv/bin/python -m pytest tests/test_quote_pdf.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@quote-pdf-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.quotes as quotes_routes
import services.r2_storage as r2_storage
import services.text_extraction as text_extraction
from models import Base, Customer, FileMetadata, PosSale, Quote, QuoteItem, get_db
from services.quote_capture import CaptureError, parse_single
from services.quote_pdf import merge_with_message, parse_quote_pdf

TABLES = [
    Customer.__table__,
    Quote.__table__,
    QuoteItem.__table__,
    PosSale.__table__,
    FileMetadata.__table__,
]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

NAME = "COT-IMPAG-390526DGO-EDWIN SANTILLANO-MALLASOMBRA 50%.pdf"
PDF_TEXT = """IMPAG TECH
Asunto:
Cotización 390526DGO
     Fecha:16/05/2026
En atención a: Edwin Santillano
Ubicación: Santiago Papasquiaro, Durango.

PRESENTE
En respuesta a su atenta solicitud, se presenta la siguiente cotización de Mallasombra.
CONCEPTO
UNIDAD
CANTIDAD
P. UNITARIO
IMPORTE
MALLA SOMBRA
ROLLO
2
$8,600.00
$17,200.00

TOTAL
$17,200.00

(DIECISIETE MIL DOCIENTOS PESOS 00/100 MXN)
Contexto: Reparación de un vivero en producción
Nota:

Cotización vigente durante 3 días hábiles.
No se hacen devoluciones totales o parciales únicamente por defectos de fábrica.
CUENTA CLABE
012 180 001193473561
"""
FAKE_PDF = b"%PDF-1.4 fake"


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    texts = {"current": PDF_TEXT}
    uploads = []
    monkeypatch.setattr(
        text_extraction, "extract_text_from_pdf_bytes", lambda b: texts["current"]
    )
    monkeypatch.setattr(
        r2_storage, "upload_file", lambda key, body, ct: uploads.append(key)
    )
    monkeypatch.setattr(
        r2_storage,
        "generate_presigned_view_url",
        lambda key, ct, expires_in=900: f"https://r2.test/{key}",
    )
    monkeypatch.setattr(quotes_routes, "_index_pdf", lambda bg, row: None)

    app = FastAPI()
    app.include_router(quotes_routes.router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    c.texts, c.uploads = texts, uploads
    return c


def _upload(client, url, name=NAME, **form):
    return client.post(
        url, files={"file": (name, FAKE_PDF, "application/pdf")}, data=form
    )


# ---------- parser ----------


def test_parses_the_team_pdf():
    pdf = parse_quote_pdf(PDF_TEXT, NAME)
    p = pdf.parsed
    assert p.quote_number == "COT-IMPAG-390526DGO"
    assert p.cliente == "Edwin Santillano"
    assert p.ubicacion == "Santiago Papasquiaro, Durango"
    assert p.material == "MALLASOMBRA 50%"  # from the file name
    assert p.total == Decimal("17200.00")
    assert pdf.fecha == date(2026, 5, 16)
    assert pdf.contexto == "Reparación de un vivero en producción"
    assert p.warnings == []


def test_grand_total_not_subtotal_and_same_line():
    text = PDF_TEXT.replace(
        "TOTAL\n$17,200.00", "SUBTOTAL $14,827.59\nIVA $2,372.41\nTOTAL: $17,200.00"
    )
    assert parse_quote_pdf(text, NAME).parsed.total == Decimal("17200.00")


def test_file_name_folio_wins_over_leftover_asunto():
    text = PDF_TEXT.replace("Cotización 390526DGO", "Cotización 380426DGO")
    p = parse_quote_pdf(text, NAME).parsed
    assert p.folio == "390526DGO"
    assert any("se usa el nombre del archivo" in w for w in p.warnings)


def test_any_file_name_uses_the_asunto_folio():
    p = parse_quote_pdf(PDF_TEXT, "scan 3.pdf").parsed
    assert p.quote_number == "COT-IMPAG-390526DGO"


def test_missing_total_is_a_warning_not_an_error():
    p = parse_quote_pdf(PDF_TEXT.replace("TOTAL\n$17,200.00", ""), NAME).parsed
    assert p.total is None
    assert any("TOTAL" in w for w in p.warnings)


def test_no_folio_anywhere_is_an_error():
    with pytest.raises(CaptureError):
        parse_quote_pdf(PDF_TEXT.replace("Cotización 390526DGO", ""), "scan.pdf")


def test_message_labels_win_pdf_fills_total():
    msg = parse_single(
        "*Cotización Enviada 390526 (Actualización)*\nCliente: Edwin S.\n"
        "Material/Proyecto: Malla 50% 3.6m"
    )
    merged = merge_with_message(parse_quote_pdf(PDF_TEXT, NAME), msg)
    assert merged.quote_number == "COT-IMPAG-390526DGO"  # state from the PDF
    assert merged.cliente == "Edwin S"  # trailing dot stripped like the paste flow
    assert merged.material == "Malla 50% 3.6m"
    assert merged.tag == "Actualización"
    assert merged.total == Decimal("17200.00")


# ---------- routes ----------


def test_capture_pdf_dry_run_writes_nothing(client):
    r = _upload(client, "/quotes/capture-pdf", dry_run="true")
    assert r.status_code == 200, r.text
    preview = r.json()["data"]["preview"]
    assert preview["action"] == "created"
    assert preview["total"] == 17200.0
    assert preview["pdf_date"] == "2026-05-16"
    db = TestingSession()
    assert db.query(Quote).count() == 0 and db.query(FileMetadata).count() == 0
    assert client.uploads == []


def test_capture_pdf_creates_quote_with_total_date_and_file(client):
    r = _upload(client, "/quotes/capture-pdf")
    assert r.status_code == 200, r.text
    quote = r.json()["data"]["quote"]
    assert quote["quote_number"] == "COT-IMPAG-390526DGO"
    assert quote["total"] == 17200.0
    assert quote["sent_at"].startswith("2026-05-16")
    assert "Contexto: Reparación de un vivero" in quote["notes"]
    assert client.uploads == [f"cotizacion/1/{NAME}"]

    files = client.get(f"/quotes/{quote['id']}/files").json()["data"]
    assert [f["filename"] for f in files] == [NAME]
    assert files[0]["view_url"] == f"https://r2.test/cotizacion/1/{NAME}"


def test_same_pdf_twice_is_stored_once(client):
    _upload(client, "/quotes/capture-pdf")
    r = _upload(client, "/quotes/capture-pdf")
    assert r.json()["data"]["preview"]["action"] == "updated"  # re-send
    assert TestingSession().query(FileMetadata).count() == 1


def test_not_a_pdf_is_rejected(client):
    r = client.post(
        "/quotes/capture-pdf", files={"file": ("x.pdf", b"hello", "application/pdf")}
    )
    assert r.status_code == 400


def _quote(**fields):
    db = TestingSession()
    q = Quote(
        quote_number=fields.pop("quote_number", "COT-IMPAG-390526DGO"),
        status="sent",
        customer_name=fields.pop("customer_name", "Edwin Santillano"),
        customer_phone="S/N",
        total=fields.pop("total", Decimal("0")),
        subtotal=Decimal("0"),
        iva_amount=Decimal("0"),
        created_by="t",
        **fields,
    )
    db.add(q)
    db.commit()
    return q.id


def test_attach_fills_a_zero_total(client):
    qid = _quote()
    r = _upload(client, f"/quotes/{qid}/files", name="cotizacion edwin.pdf")
    data = r.json()["data"]
    assert data["total_set"] == 17200.0
    assert data["quote"]["total"] == 17200.0
    assert "leído del PDF" in data["quote"]["notes"]
    # An arbitrary name gets the folio prefix so the quote finds it again.
    assert data["files"][0]["filename"] == "COT-IMPAG-390526DGO-cotizacion edwin.pdf"


def test_attach_keeps_an_existing_total(client):
    qid = _quote(total=Decimal("9000"))
    data = _upload(client, f"/quotes/{qid}/files").json()["data"]
    assert data["total_set"] is None
    assert data["quote"]["total"] == 9000.0


def test_attach_other_folio_warns_and_keeps_total(client):
    qid = _quote(quote_number="COT-IMPAG-400926DGO")
    data = _upload(client, f"/quotes/{qid}/files").json()["data"]
    assert data["total_set"] is None
    assert any("390526DGO" in w for w in data["warnings"])


def test_reused_folio_shows_the_customers_own_pdf(client):
    db = TestingSession()
    for i, name in enumerate(
        [
            "COT-IMPAG-070326DGO-MIGUEL ESCOBAR-BOMBA.pdf",
            "COT-IMPAG-070326DGO-VICTOR-MALLA.pdf",
        ]
    ):
        db.add(
            FileMetadata(
                file_key=f"cotizacion/{i}/{name}",
                original_filename=name,
                content_type="application/pdf",
                file_size_bytes=10,
                category="cotizacion",
                uploaded_by_email="t",
            )
        )
    db.commit()
    qid = _quote(quote_number="COT-IMPAG-070326DGO", customer_name="Miguel Escobar")
    files = client.get(f"/quotes/{qid}/files").json()["data"]
    assert [f["filename"] for f in files] == [
        "COT-IMPAG-070326DGO-MIGUEL ESCOBAR-BOMBA.pdf"
    ]
