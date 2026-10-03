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
LABEL_RE = re.compile(
    r"(?P<label>cliente|ubicaci[oó]n|entrega|material\s*/\s*proyecto|"
    r"tel[eé]fono|tel|total)\s*:",
    re.IGNORECASE,
)

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

        labels = list(LABEL_RE.finditer(body))
        for j, label in enumerate(labels):
            value_end = labels[j + 1].start() if j + 1 < len(labels) else len(body)
            value = _clean(body[label.end() : value_end])
            key = label.group("label").lower()
            if key == "cliente":
                item.cliente = value
            elif key.startswith("ubicaci"):
                item.ubicacion = value
            elif key == "entrega":
                item.entrega = value
            elif key.startswith("material"):
                item.material = value
            elif key.startswith("tel"):
                item.telefono = value
            elif key == "total":
                item.total = parse_money(value)

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
    """Create the quote, or re-send the one with the same folio. Returns
    {"action": "created"|"updated", "quote": Quote|None, "warnings": [...],
    "existing": Quote|None}. dry_run never writes."""
    warnings = list(parsed.warnings)
    total = total if total is not None else parsed.total
    phone_e164 = normalize_customer_phone(phone or parsed.telefono)
    if (phone or parsed.telefono) and not phone_e164:
        warnings.append("No entendí el teléfono; se guarda sin teléfono.")
    when = sent_at_for(sent_date)
    existing = find_existing(db, parsed)
    material = parsed.material or "—"

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
