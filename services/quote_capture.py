"""Register a quote from Hernán's WhatsApp *Cotización Enviada* message.

The team quotes outside the app (Balance de Venta + PDF) and announces each one
in the Operaciones group with a hand-typed template:

    *Cotización Enviada 400926DGO (Actualización)*
    Cliente: Miguel Cordero
    Ubicación: Nuevo Ideal, Dgo
    Entrega: *Nuevo Ideal, Dgo*
    Material/Proyecto:
    Bolsa para vivero 17x17

Pasting that message into the admin creates (or, for a folio that already
exists, re-sends) a trackable Quote so the pipeline, the status board and the
follow-up sweep (services/quote_followup.py) see it. The template carries no
total or phone — those come from the form.

Folio NNMMYY[EDO] -> quote_number COT-IMPAG-NNMMYY[EDO], the same numbering
scripts/backfill_open_quotes.py used for the PDFs (<= 20 chars).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from models import Customer, Quote

BUSINESS_TZ = ZoneInfo("America/Mexico_City")  # same business clock as routes/pos.py
QUOTE_PREFIX = "COT-IMPAG-"
NO_PHONE = "S/N"  # what the PDF backfill stores when there is no phone

# Statuses a re-sent folio revives back to "sent". An accepted quote stays
# accepted (a sale already closed it); drafts never carry a COT-IMPAG number.
REOPEN_ON_RESEND = ("sent", "viewed", "needs_work", "rejected", "expired")

# "*Cotización Enviada 400926DGO (Actualización)*" — accent, case, bold, the z
# ("Cotiacion" happens) and a space before the state are all optional. The
# state may not run into the next word ("150626\nCliente" must not read "CL").
HEADER_RE = re.compile(
    r"\*?\s*coti[sz]?aci[oó]n\s+enviada\s*:?\s*"
    r"(?P<digits>\d{6})[ ]?(?P<state>[A-Za-z]{2,4})?(?![A-Za-z0-9])"
    r"\s*(?:\((?P<tag>[^)\n]{1,40})\))?\s*\*?",
    re.IGNORECASE,
)

# Field labels Hernán uses. Matched anywhere (not only at line start) so a
# message flattened onto one line still parses.
# The July 2026 version said "Entrega en:", "Material:" and "Monto Total:";
# the *Solicitud de Cotización* says "Proyecto/Material:", "Ubicaciónes:" and
# "Datos:".
LABEL_RE = re.compile(
    r"(?P<label>cliente|ubicaci[oó]n(?:es)?|entrega(?:\s+en)?|"
    r"(?:proyecto\s*/\s*)?material(?:\s*/\s*proyecto)?|"
    r"tel[eé]fono|tel|(?:monto\s+)?total|datos)\s*:",
    re.IGNORECASE,
)

# "*Solicitud de Cotización*" — a customer asked for a quote that is not made
# yet. It has no folio; it becomes a "requested" quote (Por cotizar) that the
# *Cotización Enviada* for the same customer later turns into the real one.
REQUEST_RE = re.compile(
    r"\*?\s*solicitud\s+de\s+coti[sz]?aci[oó]n\s*:?\s*\*?", re.IGNORECASE
)
REQUEST_PREFIX = "SOL-"
REQUEST_STATUS = "requested"

# "Camila Ortiz Aviña +52 393 131 2326v": a phone typed after the name.
PHONE_AFTER_NAME_RE = re.compile(r"\s*(\+?\d[\d \-()]{8,}\d)\s*[a-z]?\s*$", re.I)

# "[19:48, 2/10/2026] Impag Tech: " — present when copied from WhatsApp Web.
WA_PREFIX_RE = re.compile(
    r"^\s*\[\d{1,2}:\d{2},\s*[\d/]+\]\s*[^:\n]{1,60}:\s*", re.MULTILINE
)
EDITED_RE = re.compile(
    r"<\s*(this message was edited|se edit[oó] este mensaje\.?)\s*>", re.IGNORECASE
)


class CaptureError(ValueError):
    """The pasted text is not a usable *Cotización Enviada* message."""


@dataclass
class ParsedCotizacion:
    folio: str  # "400926DGO"
    quote_number: str  # "COT-IMPAG-400926DGO"
    digits: str  # "400926"
    tag: Optional[str] = None  # "Actualización", "Contraoferta"
    cliente: Optional[str] = None
    ubicacion: Optional[str] = None
    entrega: Optional[str] = None
    material: Optional[str] = None
    telefono: Optional[str] = None
    total: Optional[Decimal] = None
    datos: Optional[str] = None
    kind: str = "quote"  # "quote" (Cotización Enviada) | "request" (Solicitud)
    warnings: List[str] = field(default_factory=list)


def _clean(value: str) -> Optional[str]:
    value = EDITED_RE.sub("", value).replace("*", "").replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip(" .,;:-")
    return value or None


def parse_money(value) -> Optional[Decimal]:
    """'$12,345.50' / '12345' / 12345.5 -> Decimal, None if empty/invalid."""
    if value is None:
        return None
    text = re.sub(r"[^\d.]", "", str(value))
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


def _read_labels(body: str, item: ParsedCotizacion) -> None:
    labels = list(LABEL_RE.finditer(body))
    for j, label in enumerate(labels):
        value_end = labels[j + 1].start() if j + 1 < len(labels) else len(body)
        value = _clean(body[label.end() : value_end])
        key = re.sub(r"\s+", " ", label.group("label").lower())
        if key == "cliente":
            item.cliente = value
        elif key.startswith("ubicaci"):
            item.ubicacion = value
        elif key.startswith("entrega"):
            item.entrega = value
        elif "material" in key:
            item.material = value
        elif key.startswith("tel"):
            item.telefono = value
        elif key.endswith("total"):
            item.total = parse_money(value)
        elif key == "datos":
            item.datos = value
    if item.cliente:
        phone = PHONE_AFTER_NAME_RE.search(item.cliente)
        if phone and len(re.sub(r"\D", "", phone.group(1))) >= 10:
            item.telefono = item.telefono or phone.group(1)
            item.cliente = _clean(item.cliente[: phone.start()])


def parse_cotizaciones(text: str) -> List[ParsedCotizacion]:
    """Every *Cotización Enviada* block found in the pasted text."""
    text = WA_PREFIX_RE.sub("", text or "")
    headers = list(HEADER_RE.finditer(text))
    parsed = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[header.end() : end]

        digits = header.group("digits")
        state = (header.group("state") or "").upper()
        folio = digits + state
        item = ParsedCotizacion(
            folio=folio,
            quote_number=f"{QUOTE_PREFIX}{folio}",
            digits=digits,
            tag=_clean(header.group("tag") or ""),
        )

        _read_labels(body, item)

        month = int(digits[2:4])
        if not 1 <= month <= 12:
            item.warnings.append(
                f"El folio {folio} no parece NNMMAA (mes {month:02d})."
            )
        parsed.append(item)
    return parsed


def parse_single(text: str) -> ParsedCotizacion:
    found = parse_cotizaciones(text)
    if not found:
        raise CaptureError(
            "No encontré un mensaje de *Cotización Enviada* con folio "
            "(ej. «Cotización Enviada 400926DGO»)."
        )
    if len(found) > 1:
        folios = ", ".join(p.folio for p in found)
        raise CaptureError(
            f"Pega una cotización a la vez (encontré {len(found)}: {folios})."
        )
    item = found[0]
    if not item.cliente:
        raise CaptureError(f"El mensaje {item.folio} no trae «Cliente:».")
    return item


def parse_request(text: str) -> ParsedCotizacion:
    """One *Solicitud de Cotización* message (no folio yet)."""
    text = WA_PREFIX_RE.sub("", text or "")
    headers = list(REQUEST_RE.finditer(text))
    if not headers:
        raise CaptureError("No encontré un mensaje de *Solicitud de Cotización*.")
    if len(headers) > 1:
        raise CaptureError("Pega una solicitud a la vez.")
    item = ParsedCotizacion(folio="", quote_number="", digits="", kind="request")
    _read_labels(text[headers[0].end() :], item)
    if not item.cliente:
        raise CaptureError("La solicitud no trae «Cliente:».")
    return item


def parse_message(text: str) -> ParsedCotizacion:
    """*Cotización Enviada* (a quote that went out) or *Solicitud de
    Cotización* (one still to be made)."""
    if HEADER_RE.search(text or ""):
        return parse_single(text)
    if REQUEST_RE.search(text or ""):
        return parse_request(text)
    raise CaptureError(
        "No encontré un mensaje de *Cotización Enviada* con folio "
        "(ej. «Cotización Enviada 400926DGO») ni una *Solicitud de Cotización*."
    )


def _name_tokens(name: Optional[str]) -> set:
    import unicodedata

    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return {t for t in re.findall(r"[a-z]{3,}", plain.lower())}


def same_customer(a: Optional[str], b: Optional[str]) -> bool:
    """Two names for the same person: 2+ shared words, or one name's words
    all inside the other ("Camila" vs "Camila Ortiz Aviña")."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 2 or shared == ta or shared == tb


def find_open_request(
    db: Session, phone_e164: Optional[str], cliente: Optional[str]
) -> Optional[Quote]:
    """The customer's pending request (Por cotizar): same phone first, else
    the same name — only when exactly one request matches."""
    open_requests = db.query(Quote).filter(Quote.status == REQUEST_STATUS).all()
    if phone_e164:
        by_phone = [q for q in open_requests if q.customer_phone == phone_e164]
        if len(by_phone) == 1:
            return by_phone[0]
    by_name = [q for q in open_requests if same_customer(q.customer_name, cliente)]
    return by_name[0] if len(by_name) == 1 else None


def next_request_number(db: Session, day: date) -> str:
    """SOL-ddmmyy-N, N counting that day's requests."""
    prefix = f"{REQUEST_PREFIX}{day:%d%m%y}-"
    taken = {
        n
        for (n,) in db.query(Quote.quote_number).filter(
            Quote.quote_number.like(f"{prefix}%")
        )
    }
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def find_existing(db: Session, parsed: ParsedCotizacion) -> Optional[Quote]:
    """Same folio already registered? Exact number first, then the bare digits
    (a backfilled COT-IMPAG-030626DGO must match a pasted «030626» and vice
    versa) — but only when that is unambiguous."""
    exact = db.query(Quote).filter(Quote.quote_number == parsed.quote_number).first()
    if exact:
        return exact
    # Digits-only prefix; no LIKE metacharacters possible in "COT-IMPAG-" + 6 digits.
    candidates = (
        db.query(Quote)
        .filter(Quote.quote_number.like(f"{QUOTE_PREFIX}{parsed.digits}%"))
        .limit(3)
        .all()
    )
    candidates = [
        q
        for q in candidates
        if q.quote_number == f"{QUOTE_PREFIX}{parsed.digits}"
        or not parsed.folio[6:]  # pasted without state: any state matches
    ]
    return candidates[0] if len(candidates) == 1 else None


def normalize_customer_phone(raw: Optional[str]) -> Optional[str]:
    from scripts.backfill_customers import normalize_phone

    return (
        normalize_phone(raw)
        if raw and re.search(r"\d{7,}", re.sub(r"\D", "", raw))
        else None
    )


def sent_at_for(sent_date: Optional[date]) -> datetime:
    """Now for today (or no date); noon business time for an earlier day."""
    now = datetime.now(timezone.utc)
    if sent_date is None or sent_date >= now.astimezone(BUSINESS_TZ).date():
        return now
    return datetime.combine(sent_date, time(12, 0), tzinfo=BUSINESS_TZ).astimezone(
        timezone.utc
    )


def _stamp(when: datetime) -> str:
    return when.astimezone(BUSINESS_TZ).strftime("%d/%m/%Y")


def _append_note(quote: Quote, line: str) -> None:
    quote.notes = f"{quote.notes}\n{line}" if quote.notes else line


def apply_request(
    db: Session,
    parsed: ParsedCotizacion,
    *,
    user_email: str,
    phone: Optional[str] = None,
    sent_date: Optional[date] = None,
    dry_run: bool = False,
) -> dict:
    """Register a *Solicitud de Cotización* as a "requested" quote (Por
    cotizar), numbered SOL-ddmmyy-N, dated the day it was asked. The same
    customer asking again adds a note to their open request instead."""
    warnings = list(parsed.warnings)
    phone_e164 = normalize_customer_phone(phone or parsed.telefono)
    if (phone or parsed.telefono) and not phone_e164:
        warnings.append("No entendí el teléfono; se guarda sin teléfono.")
    when = sent_at_for(sent_date)
    material = parsed.material or "—"
    open_request = find_open_request(db, phone_e164, parsed.cliente)
    if open_request is None and phone_e164:
        quoted = (
            db.query(Quote)
            .filter(
                Quote.customer_phone == phone_e164,
                Quote.status.in_(("sent", "viewed", "needs_work")),
            )
            .first()
        )
        if quoted:
            warnings.append(
                f"Este cliente ya tiene la cotización {quoted.quote_number} abierta."
            )

    if open_request is not None:
        if not dry_run:
            _append_note(
                open_request,
                f"[Solicitud] {_stamp(when)} Material/Proyecto: {material} ({user_email})",
            )
            if parsed.ubicacion and not open_request.customer_location:
                open_request.customer_location = parsed.ubicacion
            if phone_e164 and open_request.customer_phone in (None, "", NO_PHONE):
                open_request.customer_phone = phone_e164
            db.commit()
            db.refresh(open_request)
        return {
            "action": "updated",
            "quote": None if dry_run else open_request,
            "existing": open_request,
            "warnings": warnings,
        }

    number = next_request_number(db, when.astimezone(BUSINESS_TZ).date())
    parsed.quote_number = number
    if dry_run:
        return {
            "action": "created",
            "quote": None,
            "existing": None,
            "warnings": warnings,
        }

    lines = [f"Material/Proyecto: {material}"]
    if parsed.datos:
        lines.append(f"Datos: {parsed.datos}")
    if parsed.entrega:
        lines.append(f"Entrega: {parsed.entrega}")
    lines.append(f"[Solicitud] {_stamp(when)} desde mensaje de WhatsApp ({user_email})")
    quote = Quote(
        quote_number=number,
        status=REQUEST_STATUS,
        customer_name=parsed.cliente[:200],
        customer_phone=phone_e164 or NO_PHONE,
        customer_location=parsed.ubicacion,
        validity_days=15,
        created_at=when,
        created_by=user_email,
        assigned_to=user_email,
        notes="\n".join(lines),
    )
    _set_flat_total(quote, Decimal("0"))
    if phone_e164:
        customer = db.query(Customer).filter(Customer.phone_e164 == phone_e164).first()
        if customer:
            quote.customer_id = customer.id
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return {"action": "created", "quote": quote, "existing": None, "warnings": warnings}


def convert_request(
    db: Session,
    request: Quote,
    parsed: ParsedCotizacion,
    *,
    user_email: str,
    total: Optional[Decimal] = None,
    phone_e164: Optional[str] = None,
    when: Optional[datetime] = None,
) -> Quote:
    """The quote for a pending request went out: the request row becomes the
    COT-IMPAG quote (folio, Enviada, sent date, total), keeping its history."""
    when = when or datetime.now(timezone.utc)
    request_number, request_name = request.quote_number, request.customer_name
    request.quote_number = parsed.quote_number
    request.status = "sent"
    request.sent_at = when
    request.followup_count = 0
    request.last_followup_at = None
    request.customer_name = (parsed.cliente or request.customer_name)[:200]
    if parsed.ubicacion:
        request.customer_location = parsed.ubicacion
    if phone_e164 and request.customer_phone in (None, "", NO_PHONE):
        request.customer_phone = phone_e164
    if total is not None and not request.items:
        _set_flat_total(request, total)
    if parsed.entrega:
        _append_note(request, f"Entrega: {parsed.entrega}")
    renamed = f" ({request_name})" if request_name != request.customer_name else ""
    _append_note(
        request,
        f"[Cotización] {_stamp(when)} {parsed.folio} de la solicitud "
        f"{request_number}{renamed} — Material/Proyecto: "
        f"{parsed.material or '—'} ({user_email})",
    )
    request.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    return request


def _set_flat_total(quote: Quote, total: Decimal) -> None:
    # Like the PDF backfill: the PDF total as-is, IVA not broken out.
    quote.subtotal = total
    quote.iva_amount = Decimal("0")
    quote.total = total


def apply_capture(
    db: Session,
    parsed: ParsedCotizacion,
    *,
    user_email: str,
    total: Optional[Decimal] = None,
    phone: Optional[str] = None,
    sent_date: Optional[date] = None,
    dry_run: bool = False,
) -> dict:
    """Create the quote, or re-send the one with the same folio, or turn the
    customer's pending request (Por cotizar) into it. Returns
    {"action": "created"|"updated"|"converted", "quote": Quote|None,
    "warnings": [...], "existing": Quote|None}. dry_run never writes.
    A *Solicitud de Cotización* goes to apply_request."""
    if parsed.kind == "request":
        return apply_request(
            db,
            parsed,
            user_email=user_email,
            phone=phone,
            sent_date=sent_date,
            dry_run=dry_run,
        )
    warnings = list(parsed.warnings)
    total = total if total is not None else parsed.total
    phone_e164 = normalize_customer_phone(phone or parsed.telefono)
    if (phone or parsed.telefono) and not phone_e164:
        warnings.append("No entendí el teléfono; se guarda sin teléfono.")
    when = sent_at_for(sent_date)
    existing = find_existing(db, parsed)
    material = parsed.material or "—"

    request = (
        find_open_request(db, phone_e164, parsed.cliente) if existing is None else None
    )
    if request is not None:
        request_number = request.quote_number
        if not dry_run:
            convert_request(
                db,
                request,
                parsed,
                user_email=user_email,
                total=total,
                phone_e164=phone_e164,
                when=when,
            )
        return {
            "action": "converted",
            "quote": None if dry_run else request,
            "existing": request,
            "request_number": request_number,
            "warnings": warnings,
        }

    if existing is None:
        if total is None:
            warnings.append(
                "Sin total: la cotización entra con $0 hasta que lo captures."
            )
        if dry_run:
            return {
                "action": "created",
                "quote": None,
                "existing": None,
                "warnings": warnings,
            }

        quote = Quote(
            quote_number=parsed.quote_number,
            status="sent",
            customer_name=parsed.cliente,
            customer_phone=phone_e164 or NO_PHONE,
            customer_location=parsed.ubicacion,
            validity_days=15,
            sent_at=when,
            created_by=user_email,
            assigned_to=user_email,
        )
        _set_flat_total(quote, total if total is not None else Decimal("0"))
        lines = []
        if parsed.entrega:
            lines.append(f"Entrega: {parsed.entrega}")
        lines.append(f"Material/Proyecto: {material}")
        lines.append(
            f"[Registro] {_stamp(when)} desde mensaje de WhatsApp ({user_email})"
        )
        quote.notes = "\n".join(lines)
        if phone_e164:
            customer = (
                db.query(Customer).filter(Customer.phone_e164 == phone_e164).first()
            )
            if customer:
                quote.customer_id = customer.id
        db.add(quote)
        db.commit()
        db.refresh(quote)
        return {
            "action": "created",
            "quote": quote,
            "existing": None,
            "warnings": warnings,
        }

    # Same folio again = re-sent (Actualización / Contraoferta / plain re-send).
    if existing.status == "accepted":
        warnings.append(
            "Ya estaba aceptada: se registra el reenvío pero sigue como Aceptada."
        )
    elif existing.status not in REOPEN_ON_RESEND:
        warnings.append(f"Estado actual «{existing.status}»: no se cambia.")
    if total is not None and existing.items:
        warnings.append(
            "Tiene productos capturados: el total sale de los productos, no se cambia."
        )
    if dry_run:
        return {
            "action": "updated",
            "quote": None,
            "existing": existing,
            "warnings": warnings,
        }

    if existing.status in REOPEN_ON_RESEND:
        existing.status = "sent"
        existing.expired_at = None
        existing.accepted_at = None
        existing.sent_at = when
        # A new version starts a new follow-up cycle (quote_followup caps nudges).
        existing.followup_count = 0
        existing.last_followup_at = None
    if total is not None and not existing.items:
        _set_flat_total(existing, total)
    if phone_e164 and existing.customer_phone in (None, "", NO_PHONE):
        existing.customer_phone = phone_e164
        if existing.customer_id is None:
            customer = (
                db.query(Customer).filter(Customer.phone_e164 == phone_e164).first()
            )
            if customer:
                existing.customer_id = customer.id
    if parsed.ubicacion and not existing.customer_location:
        existing.customer_location = parsed.ubicacion
    label = parsed.tag or "Reenvío"
    _append_note(
        existing,
        f"[{label}] {_stamp(when)} Material/Proyecto: {material} ({user_email})",
    )
    existing.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(existing)
    return {
        "action": "updated",
        "quote": existing,
        "existing": existing,
        "warnings": warnings,
    }
