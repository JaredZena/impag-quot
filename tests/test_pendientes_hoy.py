"""
Hermetic tests for the *PENDIENTES ddmmyy* board sync (services/pendientes.py,
POST /tasks/pendientes/sync, GET /tasks/pendientes/text) and the Hoy summary
(GET /hoy). SQLite in-memory.

Run: venv/bin/python -m pytest tests/test_pendientes_hoy.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@pendientes-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.hoy as hoy_routes
import routes.tasks_mgmt as tasks_routes
from models import (
    Base,
    Customer,
    Quote,
    QuoteItem,
    Sale,
    Task,
    TaskCategory,
    TaskComment,
    TaskUser,
    get_db,
)
from services.pendientes import PendientesError, parse_pendientes

DEV_EMAIL = "dev@local.test"
TABLES = [
    TaskUser.__table__,
    TaskCategory.__table__,
    Task.__table__,
    TaskComment.__table__,
    Customer.__table__,
    Quote.__table__,
    QuoteItem.__table__,
    Sale.__table__,
]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Hernán's 02/10/2026 list (trimmed), as copied from WhatsApp.
LIST = """[19:48, 2/10/2026] Impag Tech: PENDIENTES 021026

FACTURACION INTERNA Y EXTERNA
1. Correo Miguel Montañez

COTIZACIONES Y NOTAS
1. Cotización Baño de Vacas
2. Cotizacion Invernadero Camila Jalisco
3. Cotizacion Baterías Tepe


RASTREO, GUIAS Y PEDIDOS
1. Comprar muestra de geocostal

ENTREGAS Y STOCK EXTERNO
1. Conformacion entrega de Bolsa Ejido revolucion.

PAGOS PENDIENTES

debe IMPAG
1. Internet $349
2. Semana Hernán $2,500

deben a IMPAG
1. Leonardo Rey $1,637.50 (pidió chanza)

OTROS
1. Comprar Calculadora Para Local.
2. Actualizar CSF (Adriana)"""


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    db = TestingSession()
    db.add(TaskUser(email=DEV_EMAIL, display_name="Dev"))
    db.commit()
    app = FastAPI()
    app.include_router(tasks_routes.router)
    app.include_router(hoy_routes.router)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _open_titles():
    return sorted(
        t.title for t in TestingSession().query(Task).filter(Task.status == "pending")
    )


def test_parses_the_six_sections():
    p = parse_pendientes(LIST)
    assert p.stamp == "021026"
    assert p.items["cotizaciones"] == [
        "Cotización Baño de Vacas",
        "Cotizacion Invernadero Camila Jalisco",
        "Cotizacion Baterías Tepe",
    ]
    assert p.items["debe_impag"] == ["Internet $349", "Semana Hernán $2,500"]
    assert p.items["deben_a_impag"] == ["Leonardo Rey $1,637.50 (pidió chanza)"]
    assert p.count == 11


def test_rejects_text_without_header_or_items():
    with pytest.raises(PendientesError):
        parse_pendientes("COTIZACIONES Y NOTAS\n1. algo")
    with pytest.raises(PendientesError):
        parse_pendientes("PENDIENTES 021026\nalgo sin sección")


def test_first_sync_creates_the_board_dry_run_first(client):
    preview = client.post(
        "/tasks/pendientes/sync", json={"text": LIST, "dry_run": True}
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["data"]["create"]) == 11
    assert TestingSession().query(Task).count() == 0

    client.post("/tasks/pendientes/sync", json={"text": LIST})
    assert len(_open_titles()) == 11
    names = {c.name for c in TestingSession().query(TaskCategory)}
    assert "Pagos: deben a IMPAG" in names and "Cotizaciones y notas" in names


def test_next_day_list_keeps_edits_closes_and_adds(client):
    client.post("/tasks/pendientes/sync", json={"text": LIST})
    next_day = (
        LIST.replace("PENDIENTES 021026", "PENDIENTES 031026")
        .replace("3. Cotizacion Baterías Tepe\n", "")  # done
        .replace(
            "Comprar Calculadora Para Local.", "Comprar Calculadora Para el Local"
        )  # edited
        .replace(
            "2. Actualizar CSF (Adriana)",
            "2. Actualizar CSF (Adriana)\n3. Pintar letrero",
        )
    )
    data = client.post("/tasks/pendientes/sync", json={"text": next_day}).json()["data"]
    assert [c["title"] for c in data["close"]] == ["Cotizacion Baterías Tepe"]
    assert [c["title"] for c in data["create"]] == ["Pintar letrero"]
    titles = _open_titles()
    assert "Comprar Calculadora Para el Local" in titles  # took the new wording
    assert "Cotizacion Baterías Tepe" not in titles
    closed = TestingSession().query(Task).filter(Task.status == "done").one()
    assert closed.completed_at is not None


def test_old_category_task_moves_into_its_section(client):
    db = TestingSession()
    user = db.query(TaskUser).one()
    old = TaskCategory(name="General", created_by=user.id)
    db.add(old)
    db.flush()
    db.add(
        Task(
            title="Internet $349",
            category_id=old.id,
            created_by=user.id,
            status="pending",
        )
    )
    db.add(
        Task(
            title="Tarea zombi de febrero",
            category_id=old.id,
            created_by=user.id,
            status="pending",
        )
    )
    db.commit()
    queue = TaskCategory(name="Seguimiento a cotizaciones", created_by=user.id)
    db.add(queue)
    db.flush()
    db.add(
        Task(
            title="Cotización web WEB-260930 — revisar",
            category_id=queue.id,
            created_by=user.id,
            status="pending",
        )
    )
    db.commit()
    data = client.post("/tasks/pendientes/sync", json={"text": LIST}).json()["data"]
    assert [m["title"] for m in data["move"]] == ["Internet $349"]
    # The app's own queues (follow-ups, web orders) are never closed.
    assert "Cotización web WEB-260930 — revisar" in _open_titles()
    assert [c["title"] for c in data["close"]] == ["Tarea zombi de febrero"]
    assert len(data["create"]) == 10


def test_export_round_trips_in_the_same_format(client):
    client.post("/tasks/pendientes/sync", json={"text": LIST})
    text = client.get("/tasks/pendientes/text").json()["data"]["text"]
    assert text.startswith("PENDIENTES ")
    assert (
        "PAGOS PENDIENTES\n\ndebe IMPAG\n1. Internet $349\n2. Semana Hernán $2,500"
        in text
    )
    again = parse_pendientes(text)
    assert again.items == parse_pendientes(LIST).items


def test_hoy_collects_the_days_numbers(client):
    day = date(2026, 10, 2)
    db = TestingSession()
    user = db.query(TaskUser).one()
    db.add(
        Sale(
            sheet_tab="WHATSAPP",
            source_row=202610001,
            sale_date=day,
            customer_name="Alejandro Echeverría",
            amount=Decimal("700"),
            pending_amount=Decimal("660"),
            reference="Venta 01_10_2026",
            quarantined=False,
        )
    )
    db.add(
        Quote(
            quote_number="COT-IMPAG-031026TAB",
            status="sent",
            customer_name="VICOR CONSTRUCCIONES",
            customer_phone="S/N",
            sent_at=datetime(2026, 10, 2, 22, 0, tzinfo=timezone.utc),  # 16:00 local
            notes="Material/Proyecto: Malla ciclónica",
            created_by="t",
        )
    )
    db.add(
        Quote(
            quote_number="COT-IMPAG-090926DGO",
            status="needs_work",
            customer_name="Vladimir",
            customer_phone="S/N",
            sent_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
            notes=(
                "Material/Proyecto: Cerco solar\n"
                "[Estado] 02/10/2026 Por ajustar — Busca algo más económico (hernan@x.com)"
            ),
            created_by="t",
        )
    )
    db.add(
        Task(
            title="Entregar bolsa",
            status="done",
            completed_at=datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc),
            created_by=user.id,
        )
    )
    db.add(
        Task(
            title="Visto bueno bombeos",
            status="pending",
            priority="urgent",
            created_by=user.id,
        )
    )
    db.commit()

    data = client.get("/hoy", params={"day": "2026-10-02"}).json()["data"]
    assert [s["reference"] for s in data["sales"]] == ["Venta 01_10_2026"]
    assert data["quotes_sent"][0]["material"] == "Malla ciclónica"
    assert data["followups"][0]["customer_name"] == "Vladimir"
    assert data["followups"][0]["detail"] == "Por ajustar — Busca algo más económico"
    assert [t["title"] for t in data["closed_tasks"]] == ["Entregar bolsa"]
    assert data["receivable"]["total"] == 660.0
    assert data["priority"] == ["Visto bueno bombeos"]
