"""Hernán's daily *PENDIENTES ddmmyy* WhatsApp list as the task board.

The team runs its to-do list as one message in the Operaciones group, in six
fixed sections (PAGOS PENDIENTES split into what IMPAG owes and what it is
owed):

    PENDIENTES 021026

    FACTURACION INTERNA Y EXTERNA
    1. Correo Miguel Montañez

    COTIZACIONES Y NOTAS
    1. Cotización Baño de Vacas
    ...
    PAGOS PENDIENTES
    debe IMPAG
    1. Internet $349
    deben a IMPAG
    1. Leonardo Rey $1,637.50 (pidió chanza)

    OTROS
    1. Comprar Calculadora Para Local.

Pasting it into Pendientes syncs the board to it: new lines become tasks,
lines that are still there stay (moved if they changed section), and open
tasks that are no longer on the list are closed — the list is the truth.
render_text() writes the board back in the same format to post in the group.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from models import Task, TaskCategory, get_next_task_number

# (key, header in the message, category name in the app, color)
SECTIONS: List[Tuple[str, str, str, str]] = [
    (
        "facturacion",
        "FACTURACION INTERNA Y EXTERNA",
        "Facturación interna y externa",
        "#0ea5e9",
    ),
    ("cotizaciones", "COTIZACIONES Y NOTAS", "Cotizaciones y notas", "#6366f1"),
    ("rastreo", "RASTREO, GUIAS Y PEDIDOS", "Rastreo, guías y pedidos", "#f59e0b"),
    ("entregas", "ENTREGAS Y STOCK EXTERNO", "Entregas y stock externo", "#10b981"),
    ("debe_impag", "debe IMPAG", "Pagos: debe IMPAG", "#ef4444"),
    ("deben_a_impag", "deben a IMPAG", "Pagos: deben a IMPAG", "#f97316"),
    ("otros", "OTROS", "Otros", "#64748b"),
]
SECTION_KEYS = [s[0] for s in SECTIONS]
CATEGORY_NAME = {s[0]: s[2] for s in SECTIONS}
PAGOS_HEADER = "PAGOS PENDIENTES"
OPEN_STATUSES = ("pending", "in_progress")
MATCH_RATIO = 0.85

HEADER_RE = re.compile(r"^\s*\*?pendientes\s*(?P<stamp>\d{6})?\*?\s*$", re.I)
ITEM_PREFIX_RE = re.compile(r"^\s*(?:\d+\s*[.)-]|[-•*·]|#\.)\s*")
WA_PREFIX_RE = re.compile(r"^\s*\[\d{1,2}:\d{2},\s*[\d/]+\]\s*[^:\n]{1,60}:\s*", re.M)


class PendientesError(ValueError):
    pass


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9$]+", " ", plain.lower()).split())


def _section_of(line: str) -> Optional[str]:
    """The section a header line opens (None if it is an item)."""
    plain = normalize(line)
    if not plain:
        return None
    if plain == normalize(PAGOS_HEADER):
        return "pagos"
    if plain.startswith("deben a impag") or plain == "nos deben":
        return "deben_a_impag"
    if plain.startswith("debe impag") or plain == "debemos":
        return "debe_impag"
    for key, header, _, _ in SECTIONS:
        if plain == normalize(header):
            return key
    return None


@dataclass
class ParsedList:
    stamp: Optional[str]  # "021026"
    items: Dict[str, List[str]] = field(
        default_factory=lambda: {k: [] for k in SECTION_KEYS}
    )

    @property
    def count(self) -> int:
        return sum(len(v) for v in self.items.values())


def parse_pendientes(text: str) -> ParsedList:
    text = WA_PREFIX_RE.sub("", text or "")
    lines = [ln.rstrip() for ln in text.splitlines()]
    start = next((i for i, ln in enumerate(lines) if HEADER_RE.match(ln)), None)
    if start is None:
        raise PendientesError("No encontré el encabezado «PENDIENTES ddmmaa».")
    parsed = ParsedList(stamp=HEADER_RE.match(lines[start]).group("stamp"))
    section: Optional[str] = None
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        opened = _section_of(line)
        if opened == "pagos":
            section = "debe_impag"  # until a "deben a IMPAG" sub-header
            continue
        if opened:
            section = opened
            continue
        item = ITEM_PREFIX_RE.sub("", line).strip()
        if not item:
            continue
        if section is None:
            raise PendientesError(
                f"«{item[:60]}» está antes de cualquier sección (ej. COTIZACIONES Y NOTAS)."
            )
        parsed.items[section].append(item[:300])
    if parsed.count == 0:
        raise PendientesError("La lista no trae pendientes.")
    return parsed


def section_categories(
    db: Session, created_by: int, create: bool
) -> Dict[str, Optional[TaskCategory]]:
    """key -> TaskCategory for the six sections (created on first sync)."""
    found: Dict[str, Optional[TaskCategory]] = {}
    for order, (key, _, name, color) in enumerate(SECTIONS):
        cat = db.query(TaskCategory).filter(TaskCategory.name == name).first()
        if cat is None and create:
            cat = TaskCategory(
                name=name,
                color=color,
                created_by=created_by,
                sort_order=order,
                is_active=True,
            )
            db.add(cat)
            db.flush()
        found[key] = cat
    return found


@dataclass
class SyncPlan:
    create: List[Tuple[str, str]] = field(default_factory=list)  # (section, title)
    keep: List[Tuple[Task, str]] = field(default_factory=list)  # (task, section)
    move: List[Tuple[Task, str]] = field(default_factory=list)  # changed section
    close: List[Task] = field(default_factory=list)


def plan_sync(db: Session, parsed: ParsedList) -> SyncPlan:
    """Match every list line to an open task (same text, or ≥ MATCH_RATIO
    similar); unmatched lines are new, unmatched open tasks are closed."""
    categories = section_categories(db, created_by=0, create=False)
    section_of_category = {c.id: k for k, c in categories.items() if c is not None}
    open_tasks = db.query(Task).filter(Task.status.in_(OPEN_STATUSES)).all()
    unmatched = {t.id: t for t in open_tasks}
    plan = SyncPlan()

    for section in SECTION_KEYS:
        for title in parsed.items[section]:
            target = normalize(title)
            best, best_ratio = None, 0.0
            for task in unmatched.values():
                candidate = normalize(task.title)
                ratio = (
                    1.0
                    if candidate == target
                    else SequenceMatcher(None, candidate, target).ratio()
                )
                # Prefer the same section on ties.
                if section_of_category.get(task.category_id) == section:
                    ratio += 0.001
                if ratio > best_ratio:
                    best, best_ratio = task, ratio
            if best is not None and best_ratio >= MATCH_RATIO:
                del unmatched[best.id]
                if section_of_category.get(best.category_id) == section:
                    plan.keep.append((best, section))
                else:
                    plan.move.append((best, section))
            else:
                plan.create.append((section, title))
    plan.close = list(unmatched.values())
    return plan


def apply_sync(
    db: Session, parsed: ParsedList, plan: SyncPlan, *, user_id: int
) -> None:
    categories = section_categories(db, created_by=user_id, create=True)
    now = datetime.now(timezone.utc)
    for task, section in plan.keep + plan.move:
        task.category_id = categories[section].id
    for task, section in plan.keep + plan.move:
        title = next(
            (t for t in parsed.items[section] if normalize(t) == normalize(task.title)),
            None,
        )
        if title is None:
            # Similar but edited on the list: take the list's wording.
            for t in parsed.items[section]:
                if (
                    SequenceMatcher(None, normalize(t), normalize(task.title)).ratio()
                    >= MATCH_RATIO
                ):
                    title = t
                    break
        if title and title != task.title:
            task.title = title
    for task in plan.close:
        task.status = "done"
        task.completed_at = now
    for section, title in plan.create:
        db.add(
            Task(
                title=title,
                status="pending",
                priority="medium",
                category_id=categories[section].id,
                created_by=user_id,
                task_number=get_next_task_number(db),
            )
        )
        db.flush()  # get_next_task_number must see the one just added
    db.commit()


def board(db: Session) -> Dict[str, List[Task]]:
    """Open tasks per section, oldest first; tasks outside the six sections
    go to OTROS so nothing open is ever left out of the message."""
    categories = section_categories(db, created_by=0, create=False)
    section_of_category = {c.id: k for k, c in categories.items() if c is not None}
    grouped: Dict[str, List[Task]] = {k: [] for k in SECTION_KEYS}
    for task in (
        db.query(Task)
        .filter(Task.status.in_(OPEN_STATUSES))
        .order_by(Task.created_at, Task.id)
    ):
        grouped[section_of_category.get(task.category_id, "otros")].append(task)
    return grouped


def render_text(grouped: Dict[str, List[Task]], day: date) -> str:
    """The board as Hernán's *PENDIENTES ddmmyy* message."""
    out = [f"PENDIENTES {day:%d%m%y}"]

    def block(key: str) -> List[str]:
        return [f"{i}. {t.title}" for i, t in enumerate(grouped[key], 1)]

    for key, header, _, _ in SECTIONS:
        if key == "debe_impag":
            out += ["", PAGOS_HEADER, "", "debe IMPAG", *block("debe_impag")]
        elif key == "deben_a_impag":
            out += ["", "deben a IMPAG", *block("deben_a_impag")]
        else:
            out += ["", header, *block(key)]
    return "\n".join(out)
