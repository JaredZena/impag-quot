"""
"Clientes por tipo" on Ventas: how many buyers of each kind of producer.

Nothing stores a producer type, so each buyer in the sales ledger is
classified by WHAT THEY BOUGHT: every ledger row's concept + description is
matched against the keyword rules below (first match wins, so the order
matters: "bombeo solar para ganado" is ganadería, "lámpara solar" is hogar).
A row with no product text borrows it from the quote it closes, then from
the buyer's quotes matched by name. A buyer's type is the one they spent the
most on; the other types they bought appear in `types`.

Read-only: computed on every request from `sale` + `quote`, nothing written.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import extract
from sqlalchemy.orm import Session

from models import Customer, Quote, QuoteItem, Sale
from services.seguimiento import NOT_A_CUSTOMER_RE, _material, contact_key, fold

# (slug, label, is a producer, pattern over folded text). First match wins;
# substrings on purpose ("microaspersor", "75lph"), \b only on short words.
TYPES: list[tuple[str, str, bool, str]] = [
    (
        "ganaderia",
        "Ganadería",
        True,
        (
            r"cerco|cervo|bebeder|comeder|ganad|\bvaca|becerr|borreg|cabra|pastur|"
            r"bascula|corral|picadora|ensil|ordena|electrificador|melaza|\bpuas?\b"
        ),
    ),
    (
        "hogar_solar",
        "Hogar / energía solar",
        False,
        (
            r"lampara|pampara|luces|bateria|controlador|kit solar|navide|nixtamal|"
            r"mc4|aislado|interconect|paneles? solar"
        ),
    ),
    ("bombeo", "Bombeo solar / motobomba", True, r"bomba|bombeo|kolos|kol4|kan15"),
    (
        "agua",
        "Almacenamiento de agua",
        True,
        r"geomembrana|geomemebrana|tinaco|tanque|geotanque|\bolla|cisterna|rotoplas",
    ),
    (
        "vivero",
        "Vivero",
        True,
        r"bolsa|charola|cavidad|peat|sustrato|vivero|semilla|germin",
    ),
    (
        "riego",
        "Riego",
        True,
        (
            r"cintilla|gotero|aspersor|riego|netafim|rivulis|ro drip|acolchado|"
            r"nebuliz|valvula|tuberia|pvc|filtro|manguera|cople|conexion|drip|lph|"
            r"lay ?flat|poliducto|wobbl|canon|xcel|pivote|hidrante"
        ),
    ),
    (
        "protegida",
        "Invernadero / casa sombra",
        True,
        (
            r"plastico|agrotermic|perfil|zig ?zag|cortinero|sujetador|anillo|"
            r"invernadero|malla|sombra|monofilamento|antihelad|ground cover|tutoreo|"
            r"rafia|pija|\barco|antiafido|antiinsect|trampa|macrotunel|tunel"
        ),
    ),
]
OTHER = ("otro", "Otros productos", True)
NO_DATA = ("sin_dato", "Sin descripción de producto", True)
_COMPILED = [(slug, re.compile(pat)) for slug, _l, _p, pat in TYPES]
LABELS = {slug: label for slug, label, _p, _pat in TYPES}
LABELS.update({OTHER[0]: OTHER[1], NO_DATA[0]: NO_DATA[1]})
PRODUCER = {slug: producer for slug, _l, producer, _pat in TYPES}
PRODUCER.update({OTHER[0]: True, NO_DATA[0]: True})

# Big 2026 buyers whose sheet rows have no description and no quote to borrow
# from; checked by hand in the WhatsApp/RAG history on 2026-10-22. Key =
# contact_key(customer_name). New sales come from *Venta* messages with items,
# so this list should not need to grow.
VERIFIED: dict[str, str] = {
    "rodrigo herrera montes": "bombeo",  # bombeo solar 15 HP (quote 190626DGO)
    "world vision": "bombeo",  # 50 sistemas Kolos 48 en pozos, La Noria
    "sembrando vida el salto": "vivero",  # bolsa 17x17
    "salvador gurrola": "protegida",  # malla monofilamento
    "antonio aguilar reyes": "protegida",  # 3 rollos malla sombra 50%
    "santiago rodriguez carrera": "protegida",  # malla sombra + ground cover
    "hilaria vallez": "riego",  # kit de riego
    "claudia gloria": "vivero",  # bolsa forestal 15x25
}

SEMBRANDO_VIDA_RE = re.compile(r"\b(cac|sembrando vida)\b")
_NAME_STOP = {"de", "la", "del", "los", "las", "y", "el", "cac"}


def classify_text(text: str | None) -> str | None:
    """Type slug for a piece of product text; None when there is no text."""
    folded = fold(text)
    if not folded:
        return None
    for slug, rx in _COMPILED:
        if rx.search(folded):
            return slug
    return OTHER[0]


def _tokens(name: str | None) -> frozenset[str]:
    return frozenset(contact_key(name).split()) - _NAME_STOP


@dataclass
class _Buyer:
    name: str
    amount_by_type: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    total: float = 0.0
    sales: int = 0
    last_sale: date | None = None
    sembrando_vida: bool = False

    def primary(self, key: str) -> str:
        if key in VERIFIED:
            return VERIFIED[key]
        real = {
            t: a
            for t, a in self.amount_by_type.items()
            if t not in (OTHER[0], NO_DATA[0])
        }
        pool = real or self.amount_by_type
        return max(pool, key=lambda t: (pool[t], t))


def _quote_texts(
    db: Session,
) -> tuple[dict[int, str], list[tuple[frozenset, int | None, str]]]:
    """Product text per quote id, plus (name tokens, customer_id, text) for
    matching a buyer to their quotes by name."""
    items: dict[int, list[str]] = defaultdict(list)
    for quote_id, description in db.query(QuoteItem.quote_id, QuoteItem.description):
        if description:
            items[quote_id].append(description)
    by_id: dict[int, str] = {}
    by_name: list[tuple[frozenset, int | None, str]] = []
    for q in db.query(Quote.id, Quote.customer_name, Quote.customer_id, Quote.notes):
        text = " ".join(filter(None, [_material(q.notes), *items.get(q.id, [])]))
        if not text:
            continue
        by_id[q.id] = text
        by_name.append((_tokens(q.customer_name), q.customer_id, text))
    return by_id, by_name


def _borrowed_text(
    sale: Sale,
    by_id: dict[int, str],
    by_name: list[tuple[frozenset, int | None, str]],
) -> str:
    if sale.quote_id and sale.quote_id in by_id:
        return by_id[sale.quote_id]
    toks = _tokens(sale.customer_name)
    found = []
    for q_toks, q_customer_id, text in by_name:
        # Same customer record, or two or more name words all contained in the
        # other name: a lone first name ("MARCELO") is too common to match on.
        same_customer = bool(sale.customer_id) and q_customer_id == sale.customer_id
        same_name = (
            len(toks) >= 2 and len(q_toks) >= 2 and (toks <= q_toks or q_toks <= toks)
        )
        if same_customer or same_name:
            found.append(text)
    return " ".join(found)


def customer_types(db: Session, year: int | None = None) -> dict:
    """Buyers per type for the Ventas card (all years, or one year)."""
    query = db.query(Sale).filter(
        Sale.quarantined.is_(False), Sale.customer_name.isnot(None)
    )
    if year is not None:
        query = query.filter(extract("year", Sale.sale_date) == year)
    sales = query.all()

    by_id, by_name = _quote_texts(db)
    sv_tagged: set[int] = set()
    sv_names: list[frozenset] = []
    for c in db.query(Customer.id, Customer.display_name, Customer.tags):
        if "sembrando-vida" in (c.tags or []):
            sv_tagged.add(c.id)
            toks = _tokens(c.display_name)
            if len(toks) >= 2:
                sv_names.append(toks)

    buyers: dict[str, _Buyer] = {}
    for s in sales:
        key = contact_key(s.customer_name)
        if not key or NOT_A_CUSTOMER_RE.search(key):
            continue
        b = buyers.setdefault(key, _Buyer(name=s.customer_name.strip()))
        kind = classify_text(f"{s.concept or ''} {s.description or ''}")
        if kind is None:
            kind = classify_text(_borrowed_text(s, by_id, by_name)) or NO_DATA[0]
        amount = float(s.amount or 0)
        # A row without an amount still says what they buy: weigh it at $1.
        b.amount_by_type[kind] += max(amount, 1.0)
        b.total += amount
        b.sales += 1
        if s.sale_date and (b.last_sale is None or s.sale_date > b.last_sale):
            b.last_sale = s.sale_date
        if (s.customer_id and s.customer_id in sv_tagged) or SEMBRANDO_VIDA_RE.search(
            fold(s.customer_name)
        ):
            b.sembrando_vida = True

    groups: dict[str, list[dict]] = defaultdict(list)
    for key, b in buyers.items():
        if not b.sembrando_vida:
            toks = _tokens(b.name)
            b.sembrando_vida = any(sv <= toks for sv in sv_names)
        primary = b.primary(key)
        groups[primary].append(
            {
                "name": b.name,
                "amount": round(b.total, 2),
                "sales": b.sales,
                "last_sale": b.last_sale.isoformat() if b.last_sale else None,
                "sembrando_vida": b.sembrando_vida,
                "types": sorted(
                    t
                    for t in b.amount_by_type
                    if t not in (primary, OTHER[0], NO_DATA[0])
                ),
            }
        )

    types = []
    for slug, members in groups.items():
        members.sort(key=lambda m: (-m["amount"], m["name"]))
        types.append(
            {
                "slug": slug,
                "label": LABELS[slug],
                "producer": PRODUCER[slug],
                "customers": len(members),
                "amount": round(sum(m["amount"] for m in members), 2),
                "sembrando_vida": sum(m["sembrando_vida"] for m in members),
                "items": members,
            }
        )
    # Unclassified last, otherwise by number of buyers.
    tail = {OTHER[0]: 1, NO_DATA[0]: 2}
    types.sort(key=lambda t: (tail.get(t["slug"], 0), -t["customers"], -t["amount"]))

    return {
        "year": year,
        "total_customers": len(buyers),
        "total_amount": round(sum(b.total for b in buyers.values()), 2),
        "sembrando_vida": sum(b.sembrando_vida for b in buyers.values()),
        "types": types,
    }
