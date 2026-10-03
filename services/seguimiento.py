"""
"Seguimiento del día": the WhatsApp follow-ups the team sends each day from
Impag Local, picked by the app and shown on Hoy. Three lists, in order:

1. cotizacion: quotes sent 3–45 days ago that nobody accepted, with fewer
   than 3 nudges and none in the last 4 days (the cadence of
   services/quote_followup). A quote never nudged (followup_count 0) is due
   even if a bulk load stamped last_followup_at.
2. temporada: who bought around this date last year (15 days before to 75
   days after) and nothing in the last 60 days. Oct–Dec is plástico
   invernadero, bolsa vivero and malla sombra season.
3. inactivo: customers with ≥ $10,000 bought since 2024, nothing in the
   last 90 days and something in the last 18 months.

A day lists every quote that is due (the owner wants them all at once, not
12 a day), plus 5 season buyers and 3 inactive customers; with few quotes
the day is still DAILY_TARGET people and empty slots go to the other lists.
"Agregar 10 más" (extra) adds more season and inactive people. One card per person
(contact_key = the folded name), so three open quotes make one message.
Someone already messaged is left out for 4 days (quotes), 30 days (others)
or 180 days after "no le interesa".

Each card has a ready message (usted, signed by the sender) and, when the
app knows the number, a wa.me link that opens WhatsApp with it typed.
Sending stays a person's click: automating WhatsApp Business puts the
number at risk, and the Cloud API number is not the one customers know.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import Customer, FollowupContact, Quote, Sale, TaskUser
from services.quote_capture import BUSINESS_TZ
from services.quote_followup import (
    DEAD_AFTER_DAYS,
    MAX_FOLLOWUPS,
    PAYMENT_STATUSES_NOT_CHASED,
    REMINDER_INTERVAL_DAYS,
    STALE_DAYS,
)

DAILY_TARGET = 20
QUOTAS = (("cotizacion", 12), ("temporada", 5), ("inactivo", 3))
KINDS = tuple(k for k, _ in QUOTAS)
OUTCOMES = ("enviado", "respondio", "venta", "no_interesa")
OUTCOME_LABEL = {
    "enviado": "WhatsApp enviado",
    "respondio": "Respondió",
    "venta": "Venta",
    "no_interesa": "No le interesa",
}
COOLDOWN_DAYS = {"cotizacion": REMINDER_INTERVAL_DAYS, "temporada": 30, "inactivo": 30}
NOT_INTERESTED_DAYS = 180
SEASON_BEFORE_DAYS, SEASON_AFTER_DAYS = 15, 75
SEASON_QUIET_DAYS = 60
INACTIVE_MIN_TOTAL = Decimal(10000)
INACTIVE_QUIET_DAYS = 90
INACTIVE_MAX_DAYS = 540  # quiet longer than ~18 months: not worth the slot
SALES_SINCE = date(2024, 1, 1)

MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
# Ledger names that are not a person to message.
NOT_A_CUSTOMER_RE = re.compile(r"\b(publico|mostrador|varios|general|impag)\b")
# Organizations: greet without a first name.
ORG_RE = re.compile(
    r"\b(comunidad|empresa|ejido|cac|sembrando|mina|grupo|construcciones|"
    r"instituto|comite|buap|sa|cv|sociedad|cooperativa|forestal|ayuntamiento|"
    r"municipio|universidad|escuela|jardin|vivero|conapesca|cimentaciones)\b"
)


def fold(text: str | None) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", plain.lower())).strip()


def contact_key(name: str | None) -> str:
    """The person behind a ledger/quote name: folded, without the bracketed
    note the sheet adds ("Aron Contreras [Cecy]" → "aron contreras")."""
    return fold(re.sub(r"[\[(].*?[\])]", " ", name or ""))[:200]


def card_key(name: str | None, phone: str | None = None) -> str:
    """Who a quote card is for: the name, plus the number when known, so two
    customers both saved as "Morales" are two people."""
    wa = wa_number(phone)
    return f"{contact_key(name)}|{wa}" if wa else contact_key(name)


def wa_number(phone: str | None) -> str | None:
    """Digits for wa.me: Mexican numbers as 52 + 10 digits."""
    d = re.sub(r"\D", "", phone or "").lstrip("0")
    if d.startswith("521") and len(d) == 13:
        d = "52" + d[3:]
    if len(d) == 10:
        d = "52" + d
    return d if len(d) >= 11 else None


def _hello(name: str) -> str:
    key = contact_key(name)
    if not key or ORG_RE.search(key):
        return "Hola, buen día."
    first = re.sub(r"[\[(].*?[\])]", " ", name).split()[0]
    return f"Hola {first[:1].upper()}{first[1:].lower()}, buen día."


def _sign(sender: str | None) -> str:
    return f"Le saluda {sender} de IMPAG." if sender else "Le saludamos de IMPAG."


def calm(text: str, limit: int = 60) -> str:
    """Sheet and template capitals read as shouting inside a sentence
    ("2 SACOS FERTILIZANTE"); long names are cut at a word."""
    text = text.strip()
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) > len(letters) / 2:
        text = text.lower()
    text = re.sub(r"\b[A-ZÁÉÍÓÚÑ]{4,}\b", lambda m: m.group(0).lower(), text)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text.rstrip(" ,.;:-")


def item_text(description: str | None) -> str:
    """The first product of a ledger line, as it reads inside a sentence."""
    text = re.split(r",\s|[;|\[]", description or "")[0]
    text = re.sub(
        r"^\s*\d[\d.,]*\s*(pzas?|piezas|sacos?|rollos?|metros|mts?|kg|kilos?)?\s*(de\s+)?",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if not text:
        return "su material"
    text = calm(text)
    return text[:1].lower() + text[1:]


def _material(notes: str | None) -> str | None:
    m = re.search(r"^Material/Proyecto:\s*(.+)$", notes or "", re.MULTILINE)
    value = m.group(1).strip() if m else None
    return None if value in (None, "", "—") else value


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _money(value) -> str:
    return f"${float(value):,.0f}"


@dataclass
class Card:
    key: str
    kind: str
    customer_name: str
    phone: str | None = None
    reason: str = ""
    message: str = ""
    amount: float = 0.0
    quote_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "customer_name": self.customer_name,
            "phone": self.phone,
            "wa": wa_number(self.phone),
            "reason": self.reason,
            "message": self.message,
            "amount": self.amount,
            "quote_ids": self.quote_ids,
        }


def sender_name(db: Session, email: str | None) -> str | None:
    if not email:
        return None
    user = (
        db.query(TaskUser)
        .filter(func.lower(TaskUser.email) == email.strip().lower())
        .first()
    )
    name = (user.display_name or "").split() if user else []
    return name[0] if name else None


def _blocked(db: Session, now: datetime) -> dict[str, str]:
    """contact_key → why it is left out today (messaged recently / not interested)."""
    since = now - timedelta(days=NOT_INTERESTED_DAYS)
    blocked: dict[str, str] = {}
    rows = (
        db.query(FollowupContact)
        .filter(FollowupContact.created_at >= since)
        .order_by(FollowupContact.created_at)
        .all()
    )
    for c in rows:
        at = _aware(c.created_at)
        # A quote card's key carries the number ("morales|52677…"); the name
        # part also blocks that person on the season / inactive lists.
        for key in {c.contact_key, c.contact_key.split("|")[0]}:
            if c.outcome == "no_interesa":
                blocked[key] = "no_interesa"
            elif now - at < timedelta(days=COOLDOWN_DAYS.get(c.kind, 30)):
                blocked.setdefault(key, "reciente")
    return blocked


class _Phones:
    """Best known WhatsApp number per person: the customer master, the
    numbers on quotes, and numbers typed on earlier follow-ups."""

    def __init__(self, db: Session):
        self.by_id: dict[int, str] = {}
        self.by_key: dict[str, str] = {}
        ambiguous = set()
        for c in db.query(Customer).filter(Customer.phone_e164.isnot(None)):
            self.by_id[c.id] = c.phone_e164
            key = contact_key(c.display_name)
            if not key:
                continue
            if key in self.by_key and self.by_key[key] != c.phone_e164:
                ambiguous.add(key)
            self.by_key[key] = c.phone_e164
        for key in ambiguous:
            self.by_key.pop(key, None)
        for q in db.query(Quote.customer_name, Quote.customer_phone).order_by(Quote.id):
            if wa_number(q.customer_phone):
                self.by_key.setdefault(contact_key(q.customer_name), q.customer_phone)
        for f in db.query(FollowupContact).filter(FollowupContact.phone.isnot(None)):
            self.by_key[f.contact_key] = f.phone

    def get(self, key: str, customer_id: int | None = None, own: str | None = None):
        if wa_number(own):
            return own
        if customer_id and customer_id in self.by_id:
            return self.by_id[customer_id]
        return self.by_key.get(key)


def _quote_cards(
    db: Session, now: datetime, sender: str | None, phones: _Phones
) -> list[Card]:
    quotes = (
        db.query(Quote)
        .filter(
            Quote.status.in_(("sent", "viewed")),
            Quote.sent_at.isnot(None),
            Quote.accepted_at.is_(None),
            Quote.sent_at <= now - timedelta(days=STALE_DAYS),
            Quote.sent_at >= now - timedelta(days=DEAD_AFTER_DAYS),
            Quote.followup_count < MAX_FOLLOWUPS,
            or_(
                Quote.followup_count == 0,
                Quote.last_followup_at.is_(None),
                Quote.last_followup_at <= now - timedelta(days=REMINDER_INTERVAL_DAYS),
            ),
            or_(
                Quote.payment_status.is_(None),
                Quote.payment_status.notin_(PAYMENT_STATUSES_NOT_CHASED),
            ),
        )
        .order_by(Quote.sent_at.desc())
        .all()
    )
    grouped: dict[str, list[Quote]] = defaultdict(list)
    for q in quotes:
        name_key = contact_key(q.customer_name)
        if name_key:
            phone = phones.get(name_key, q.customer_id, q.customer_phone)
            grouped[card_key(q.customer_name, phone)].append(q)

    cards = []
    for key, qs in grouped.items():
        latest = qs[0]
        sent_at = _aware(latest.sent_at)
        sent_local = sent_at.astimezone(BUSINESS_TZ)
        materials = []
        for q in qs:
            m = _material(q.notes)
            m = calm(m, 50) if m else None
            if m and m not in materials:
                materials.append(m)
        what = " y ".join(materials[:2]) if materials else None
        cotizacion = f"la cotización de {what}" if what else "la cotización"
        expired = now > sent_at + timedelta(days=latest.validity_days or 15)
        if latest.followup_count or expired:
            ask = (
                f"{cotizacion[:1].upper()}{cotizacion[1:]} que le enviamos el "
                f"{sent_local:%d/%m} ya pasó su vigencia; si sigue interesado se la "
                "actualizamos con precios de hoy. ¿Le interesa?"
            )
        else:
            ask = (
                f"¿Pudo revisar {cotizacion} que le enviamos el {sent_local:%d/%m}? "
                "Si gusta la ajustamos o le resolvemos cualquier duda."
            )
        total = sum(float(q.total or 0) for q in qs)
        nudge = latest.followup_count + 1
        reason = " · ".join(
            filter(
                None,
                [
                    latest.quote_number.replace("COT-IMPAG-", ""),
                    what,
                    f"enviada {sent_local:%d/%m}",
                    _money(total) if total else None,
                    f"seguimiento #{nudge}",
                    f"+{len(qs) - 1} cotización(es)" if len(qs) > 1 else None,
                ],
            )
        )
        cards.append(
            Card(
                key=key,
                kind="cotizacion",
                customer_name=latest.customer_name,
                phone=phones.get(
                    contact_key(latest.customer_name),
                    latest.customer_id,
                    latest.customer_phone,
                ),
                reason=reason,
                message=f"{_hello(latest.customer_name)} {_sign(sender)} {ask}",
                amount=total,
                quote_ids=[q.id for q in qs],
            )
        )
    return cards


def _sales_cards(
    db: Session, today: date, sender: str | None, phones: _Phones, skip: set
) -> dict[str, list[Card]]:
    rows = (
        db.query(Sale)
        .filter(
            Sale.quarantined.is_(False),
            Sale.sale_date >= SALES_SINCE,
            Sale.customer_name.isnot(None),
        )
        .all()
    )
    people: dict[str, list[Sale]] = defaultdict(list)
    for s in rows:
        key = contact_key(s.customer_name)
        if key and key not in skip and not NOT_A_CUSTOMER_RE.search(key):
            people[key].append(s)

    anchor = today - timedelta(days=365)
    win_from = anchor - timedelta(days=SEASON_BEFORE_DAYS)
    win_to = anchor + timedelta(days=SEASON_AFTER_DAYS)
    season, inactive = [], []
    for key, sales in people.items():
        last = max(s.sale_date for s in sales)
        name = max(sales, key=lambda s: s.sale_date).customer_name.strip()
        customer_id = next((s.customer_id for s in sales if s.customer_id), None)
        phone = phones.get(key, customer_id)
        in_season = [s for s in sales if win_from <= s.sale_date <= win_to]
        if in_season and (today - last).days > SEASON_QUIET_DAYS:
            top = max(in_season, key=lambda s: s.amount or 0)
            amount = float(sum(s.amount or 0 for s in in_season))
            month = f"{MONTHS[top.sale_date.month - 1]} del año pasado"
            season.append(
                Card(
                    key=key,
                    kind="temporada",
                    customer_name=name,
                    phone=phone,
                    reason=(
                        f"Compró en {MONTHS[top.sale_date.month - 1][:3]} "
                        f"{top.sale_date.year}: {item_text(top.description)} · {_money(amount)}"
                    ),
                    message=(
                        f"{_hello(name)} {_sign(sender)} En {month} nos compró "
                        f"{item_text(top.description)}. ¿Lo va a necesitar esta temporada? "
                        "Ya tenemos disponible y con gusto le cotizamos."
                    ),
                    amount=amount,
                )
            )
            continue
        total = sum((s.amount or Decimal(0)) for s in sales)
        if (
            total >= INACTIVE_MIN_TOTAL
            and INACTIVE_QUIET_DAYS < (today - last).days <= INACTIVE_MAX_DAYS
        ):
            last_sales = [s for s in sales if s.sale_date == last]
            top = max(last_sales, key=lambda s: s.amount or 0)
            inactive.append(
                Card(
                    key=key,
                    kind="inactivo",
                    customer_name=name,
                    phone=phone,
                    reason=f"{_money(total)} desde 2024 · última compra {last:%d/%m/%Y}",
                    message=(
                        f"{_hello(name)} {_sign(sender)} Hace tiempo que no sabemos de "
                        f"usted, ¿cómo le fue con {item_text(top.description)}? Si necesita "
                        "algo para esta temporada (plástico, malla sombra, bolsa para "
                        "vivero, riego) con gusto le cotizamos."
                    ),
                    amount=float(total),
                )
            )
    season.sort(key=lambda c: -c.amount)
    inactive.sort(key=lambda c: -c.amount)
    return {"temporada": season, "inactivo": inactive}


def todays_contacts(db: Session, day: date) -> list[FollowupContact]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=BUSINESS_TZ).astimezone(
        timezone.utc
    )
    return (
        db.query(FollowupContact)
        .filter(
            FollowupContact.created_at >= start,
            FollowupContact.created_at < start + timedelta(days=1),
        )
        .order_by(FollowupContact.created_at)
        .all()
    )


def daily_list(
    db: Session,
    *,
    sender_email: str | None = None,
    now: datetime | None = None,
    extra: int = 0,
) -> dict:
    """Today's follow-up cards, minus the people already messaged today."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(BUSINESS_TZ).date()
    done = todays_contacts(db, today)
    done_by_kind = defaultdict(int)
    for c in done:
        done_by_kind[c.kind] += 1

    sender = sender_name(db, sender_email)
    phones = _Phones(db)
    blocked = _blocked(db, now)
    lists = {"cotizacion": _quote_cards(db, now, sender, phones)}
    quote_names = {c.key.split("|")[0] for c in lists["cotizacion"]}
    lists.update(_sales_cards(db, today, sender, phones, skip=quote_names))
    for kind in KINDS:
        lists[kind] = [c for c in lists[kind] if c.key not in blocked]

    # Every due quote is listed today, not 12 a day: its quota grows to what is
    # due and the day grows with it.
    quotas = dict(QUOTAS)
    quotas["cotizacion"] = max(
        quotas["cotizacion"], done_by_kind["cotizacion"] + len(lists["cotizacion"])
    )
    target = max(DAILY_TARGET, sum(quotas.values())) + max(0, extra)
    remaining = max(0, target - len(done))

    picked: dict[str, list[Card]] = {k: [] for k in KINDS}
    seen = set()

    def take(kind: str, n: int):
        for card in lists[kind]:
            if n <= 0 or sum(len(v) for v in picked.values()) >= remaining:
                return
            if card.key in seen:
                continue
            seen.add(card.key)
            picked[kind].append(card)
            n -= 1

    for kind, quota in quotas.items():
        take(kind, quota - done_by_kind[kind])
    for kind in KINDS:  # empty slots go to whichever list still has people
        take(kind, remaining)

    return {
        "day": today.isoformat(),
        "target": target,
        "available": {k: len(lists[k]) for k in KINDS},
        "done": [contact_dict(c) for c in done],
        "todo": [card.as_dict() for kind in KINDS for card in picked[kind]],
    }


def contact_dict(c: FollowupContact) -> dict:
    return {
        "id": c.id,
        "key": c.contact_key,
        "kind": c.kind,
        "customer_name": c.customer_name,
        "phone": c.phone,
        "wa": wa_number(c.phone),
        "quote_id": c.quote_id,
        "outcome": c.outcome,
        "message": c.message,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


class SeguimientoError(ValueError):
    pass


def _note(quote: Quote, line: str) -> None:
    quote.notes = f"{quote.notes}\n{line}" if quote.notes else line


def log_contact(
    db: Session,
    *,
    customer_name: str,
    kind: str,
    user_email: str,
    key: str | None = None,
    quote_ids: list[int] | None = None,
    phone: str | None = None,
    message: str | None = None,
    outcome: str = "enviado",
    now: datetime | None = None,
) -> FollowupContact:
    """Record a WhatsApp follow-up as sent. On quotes it counts as a nudge
    (followup_count / last_followup_at, like the sweep) and fills a missing
    phone with the one typed. outcome="no_interesa" takes the person off the
    list without a message (see set_outcome)."""
    if kind not in KINDS:
        raise SeguimientoError("Tipo de seguimiento no válido")
    if outcome not in ("enviado", "no_interesa"):
        raise SeguimientoError("Resultado no válido")
    if not contact_key(customer_name):
        raise SeguimientoError("Falta el nombre del cliente")
    key = (key or contact_key(customer_name)).strip()[:200]
    if phone and not wa_number(phone):
        raise SeguimientoError("Teléfono no válido (10 dígitos)")
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(BUSINESS_TZ)
    quotes = db.query(Quote).filter(Quote.id.in_(quote_ids)).all() if quote_ids else []
    for q in quotes:
        if phone and not wa_number(q.customer_phone):
            q.customer_phone = f"+{wa_number(phone)}"
        if outcome == "enviado":
            q.last_followup_at = now
            q.followup_count = (q.followup_count or 0) + 1
            _note(q, f"[Seguimiento] {stamp:%d/%m/%Y} WhatsApp enviado ({user_email})")
        q.updated_at = now
    contact = FollowupContact(
        contact_key=key,
        customer_name=customer_name.strip()[:200],
        phone=f"+{wa_number(phone)}" if phone else None,
        kind=kind,
        quote_id=quotes[0].id if quotes else None,
        outcome="enviado",
        message=((message or "")[:2000] or None) if outcome == "enviado" else None,
        created_by=user_email,
        created_at=now,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    if outcome == "no_interesa":
        return set_outcome(db, contact.id, outcome, user_email=user_email, now=now)
    return contact


def set_outcome(
    db: Session,
    contact_id: int,
    outcome: str,
    *,
    user_email: str,
    now: datetime | None = None,
) -> FollowupContact:
    """Respondió / Venta / No le interesa. "No le interesa" on a quote closes
    it as Perdida, with the same audit line as a manual status change."""
    if outcome not in OUTCOMES:
        raise SeguimientoError("Resultado no válido")
    contact = db.query(FollowupContact).filter(FollowupContact.id == contact_id).first()
    if not contact:
        raise SeguimientoError("Seguimiento no encontrado")
    now = now or datetime.now(timezone.utc)
    contact.outcome = outcome
    contact.updated_at = now
    if outcome == "no_interesa" and contact.kind == "cotizacion":
        stamp = now.astimezone(BUSINESS_TZ)
        name_key, _, wa = contact.contact_key.partition("|")
        quotes = [
            q
            for q in db.query(Quote).filter(Quote.status.in_(("sent", "viewed")))
            if q.id == contact.quote_id
            or (
                contact_key(q.customer_name) == name_key
                and (not wa or wa_number(q.customer_phone) in (None, wa))
            )
        ]
        for q in quotes:
            q.status = "rejected"
            q.accepted_at = None
            q.updated_at = now
            _note(
                q,
                f"[Estado] {stamp:%d/%m/%Y} Perdida — no le interesa "
                f"(seguimiento por WhatsApp) ({user_email})",
            )
    db.commit()
    db.refresh(contact)
    return contact
