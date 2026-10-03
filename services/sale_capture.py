"""Register a sale from Hernán's WhatsApp *Venta NN_MM_YYYY* message.

The team announces every sale in the Operaciones group with a hand-typed
template (NN = the month's sale number, the same one the VENTAS sheet stores
as NOTA DE COMPRA folio NNMMYYDGO):

    Venta 01_10_2026 (Actualización)
    2 Reducciones Galv Rosc 3" a 2" $350.00/ Pieza.
    1 Kit de Accesorios para conectar motobomba
    Total: $4,595.00
    Anticipo 1: $40.00 (01/10/2026) [Efectivo]
    Anticipo 2: $4,635.00 (02/10/2026) [Efectivo]
    Pendientes: $00.00
    Alejandro Echeverría
    Nuevo Ideal, Durango

From 2026-10-01 (SALES_WHATSAPP_CUTOVER) this message is the sales ledger: it
becomes one `sale` row (sheet_tab='WHATSAPP', source_row=YYYYMMNNN) with its
payments, what is still owed and the quote it closes. The sheet's row for the
same folio is quarantined as a duplicate (services/sales_sync.py), and sheet
rows after the cutover are quarantined until their *Venta* is registered.
Pasting the same Venta again (Actualización, new anticipo) updates the row.
"""

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from models import Quote, Sale
from services.quote_capture import (
    BUSINESS_TZ,
    WA_PREFIX_RE,
    _append_note,
    _set_flat_total,
    _stamp,
    parse_money,
    same_customer,
)

SHEET_TAB = "WHATSAPP"
BRANCH = "DGO"  # every Venta folio in the sheet is NNMMYYDGO
CUTOVER = date.fromisoformat(os.getenv("SALES_WHATSAPP_CUTOVER", "2026-10-01"))
QUOTE_LINK_DAYS = 120  # a sale closes a quote sent at most this long before

HEADER_RE = re.compile(
    r"\*?\s*venta\s+(?P<nn>\d{1,3})\s*_\s*(?P<mm>\d{1,2})\s*_\s*(?P<yyyy>\d{4})"
    r"\s*(?:\((?P<tag>[^)\n]{1,40})\))?\s*\*?",
    re.IGNORECASE,
)
AMOUNT = r"\$?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)"
TOTAL_RE = re.compile(rf"^total\s*:?\s*{AMOUNT}(?P<rest>.*)$", re.I)
PAYMENT_RE = re.compile(
    rf"^(?P<label>(?:anticipo|abono|pago|liquidaci[oó]n|finiquito)(?:\s*\d+)?)\s*:?\s*{AMOUNT}(?P<rest>.*)$",
    re.I,
)
PENDING_RE = re.compile(
    rf"^(?:pendientes?|saldo(?:\s+pendiente)?|resta(?:nte)?|por\s+pagar)\s*:?\s*{AMOUNT}(?P<rest>.*)$",
    re.I,
)
NOTE_RE = re.compile(r"^nota\s*:\s*(?P<text>.*)$", re.I)
DATE_RE = re.compile(r"\(\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\s*\)")
METHOD_RE = re.compile(r"\[\s*([^\]]+?)\s*\]")
# Keywords a one-line paste is split on.
SPLIT_RE = re.compile(
    r"\s+(?=(?:total|anticipo\s*\d*|abono\s*\d*|pago\s*\d*|liquidaci[oó]n|"
    r"pendientes?|saldo|nota)\s*:)",
    re.I,
)
UNITS = {
    "m",
    "mt",
    "mts",
    "metro",
    "metros",
    "kg",
    "kgs",
    "kilo",
    "kilos",
    "pza",
    "pzas",
    "pieza",
    "piezas",
    "paq",
    "paquete",
    "paquetes",
    "rollo",
    "rollos",
    "lt",
    "lts",
    "litro",
    "litros",
    "caja",
    "cajas",
    "bulto",
    "bultos",
    "saco",
    "sacos",
    "costal",
    "costales",
    "tramo",
    "tramos",
    "juego",
    "juegos",
}
ITEM_PRICE_RE = re.compile(
    r"\$\s*(?P<price>\d[\d,]*(?:\.\d+)?)\s*(?:/\s*\.?\s*\w+\.?)?\s*\.?\s*$"
)
ITEM_QTY_RE = re.compile(r"^(?P<qty>\d+(?:[.,]\d+)?)\s+(?P<rest>.+)$")
PAYMENT_METHODS = {
    "efectivo": "efectivo",
    "transferencia": "transferencia",
    "transf": "transferencia",
    "deposito": "deposito",
    "depósito": "deposito",
    "terminal": "terminal",
    "tarjeta": "terminal",
}


class VentaError(ValueError):
    """The pasted text is not a usable *Venta* message."""


@dataclass
class VentaItem:
    description: str
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None


@dataclass
class VentaPayment:
    label: str
    amount: Decimal
    date: Optional[date] = None
    method: Optional[str] = None


@dataclass
class ParsedVenta:
    nn: int
    mm: int
    yyyy: int
    tag: Optional[str] = None
    items: List[VentaItem] = field(default_factory=list)
    total: Optional[Decimal] = None
    payments: List[VentaPayment] = field(default_factory=list)
    pending: Optional[Decimal] = None
    customer: Optional[str] = None
    location: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:  # "16_09_2026", as the team writes it
        return f"{self.nn:02d}_{self.mm:02d}_{self.yyyy}"

    @property
    def folio(self) -> str:  # "160926DGO", the sheet's NOTA DE COMPRA folio
        return f"{self.nn:02d}{self.mm:02d}{self.yyyy % 100:02d}{BRANCH}"

    @property
    def source_row(self) -> int:  # 202609016: one ledger row per Venta
        return int(f"{self.yyyy}{self.mm:02d}{self.nn:03d}")

    @property
    def paid(self) -> Decimal:
        return sum((p.amount for p in self.payments), Decimal("0"))

    @property
    def sale_date(self) -> Optional[date]:
        """First payment's date (the sheet dates a sale by its first anticipo)."""
        dated = [p.date for p in self.payments if p.date]
        return min(dated) if dated else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("*", "")).strip(" .,;:-")


def _date(rest: str) -> Optional[date]:
    m = DATE_RE.search(rest or "")
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return date(y + 2000 if y < 100 else y, mo, d)
    except ValueError:
        return None


def normalize_method(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    plain = raw.strip().lower()
    for key, value in PAYMENT_METHODS.items():
        if plain.startswith(key):
            return value
    return plain[:30]


def _method(rest: str) -> Optional[str]:
    m = METHOD_RE.search(rest or "")
    return normalize_method(m.group(1)) if m else None


def _leftover(rest: str) -> Optional[str]:
    """Text after a money line's amount/(date)/[method]: in a one-line paste
    the customer and place follow the last one."""
    text = _clean(METHOD_RE.sub(" ", DATE_RE.sub(" ", rest or "")))
    return text if re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2}", text) else None


def _item(line: str) -> VentaItem:
    text = _clean(line)
    item = VentaItem(description=text)
    price = ITEM_PRICE_RE.search(text)
    if price:
        item.unit_price = parse_money(price.group("price"))
        text = _clean(text[: price.start()])
    qty = ITEM_QTY_RE.match(text)
    if qty:
        item.quantity = Decimal(qty.group("qty").replace(",", "."))
        first, _, rest = qty.group("rest").partition(" ")
        if first.lower().strip(".") in UNITS and rest:
            item.unit = first.strip(".")
    item.description = text
    return item


def parse_venta(text: str) -> ParsedVenta:
    text = WA_PREFIX_RE.sub("", text or "")
    headers = list(HEADER_RE.finditer(text))
    if not headers:
        raise VentaError(
            "No encontré un mensaje de *Venta* con folio (ej. «Venta 16_09_2026»)."
        )
    if len(headers) > 1:
        raise VentaError(f"Pega una venta a la vez (encontré {len(headers)}).")
    header = headers[0]
    nn, mm, yyyy = (int(header.group(k)) for k in ("nn", "mm", "yyyy"))
    if not 1 <= mm <= 12 or nn < 1:
        raise VentaError(
            f"El folio Venta {header.group(0).strip()} no parece NN_MM_AAAA."
        )
    venta = ParsedVenta(
        nn=nn, mm=mm, yyyy=yyyy, tag=_clean(header.group("tag") or "") or None
    )

    body = text[header.end() :]
    if "\n" not in body.strip():
        body = SPLIT_RE.sub("\n", body)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]

    seen_money = False
    tail: List[str] = []
    for line in lines:
        money = TOTAL_RE.match(line) or PAYMENT_RE.match(line) or PENDING_RE.match(line)
        if money and _leftover(money.group("rest")):
            tail = [_leftover(money.group("rest"))]
        total = TOTAL_RE.match(line)
        payment = PAYMENT_RE.match(line)
        pending = PENDING_RE.match(line)
        note = NOTE_RE.match(line)
        if note:
            venta.notes.append(_clean(note.group("text")))
        elif total:
            seen_money = True
            venta.total = parse_money(total.group("amount"))
            when, method = _date(total.group("rest")), _method(total.group("rest"))
            if (
                when or method
            ):  # "Total: $580.00 (30/09/2026) [Efectivo]" = paid in full
                venta.payments.append(VentaPayment("Pago", venta.total, when, method))
        elif payment:
            seen_money = True
            amount = parse_money(payment.group("amount"))
            if amount is not None:
                venta.payments.append(
                    VentaPayment(
                        _clean(payment.group("label")).capitalize(),
                        amount,
                        _date(payment.group("rest")),
                        _method(payment.group("rest")),
                    )
                )
        elif pending:
            seen_money = True
            venta.pending = parse_money(pending.group("amount"))
        elif not seen_money:
            venta.items.append(_item(line))
        else:
            tail.append(_clean(line))

    if venta.total is None:
        raise VentaError(f"La Venta {venta.label} no trae «Total:».")
    if tail:
        venta.customer = tail[0]
        venta.location = ", ".join(tail[1:]) or None
        if "\n" not in text[header.end() :].strip() and not venta.location:
            venta.warnings.append(
                "Mensaje en una sola línea: revisa cliente y ubicación."
            )
    if not venta.customer:
        raise VentaError(f"La Venta {venta.label} no trae el nombre del cliente.")

    pending = venta.pending
    if pending is not None and venta.payments and venta.paid + pending != venta.total:
        venta.warnings.append(
            f"Pagos ${venta.paid:,.2f} + pendiente ${pending:,.2f} ≠ total "
            f"${venta.total:,.2f}: revisa las cantidades."
        )
    elif pending is None and venta.payments and venta.paid > venta.total:
        venta.warnings.append(
            f"Los pagos (${venta.paid:,.2f}) suman más que el total (${venta.total:,.2f})."
        )
    return venta


def pending_of(venta: ParsedVenta) -> Decimal:
    if venta.pending is not None:
        return venta.pending
    return max(venta.total - venta.paid, Decimal("0"))


def _tokens(name: Optional[str]) -> set:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return {t for t in re.findall(r"[a-z]{3,}", text.lower())}


def find_quote(
    db: Session, venta: ParsedVenta, sale_day: date, current: Optional[Sale]
) -> Optional[Quote]:
    """The quote this sale closes: same customer, sent within QUOTE_LINK_DAYS
    before the sale, not yet closed by another sale. Only when unambiguous."""
    if current is not None and current.quote_id:
        return db.query(Quote).filter(Quote.id == current.quote_id).first()
    taken = {
        qid for (qid,) in db.query(Sale.quote_id).filter(Sale.quote_id.isnot(None))
    }
    since = datetime.combine(
        sale_day - timedelta(days=QUOTE_LINK_DAYS), time(0), tzinfo=timezone.utc
    )

    def recent(q: Quote) -> bool:
        when = q.sent_at or q.created_at
        if when is None:
            return False
        if when.tzinfo is None:  # SQLite hands back naive datetimes
            when = when.replace(tzinfo=timezone.utc)
        return when >= since

    def same_buyer(q: Quote) -> bool:
        # Full-name match, or a shared surname AND the same amount
        # ("Jesús Montañez" quoted $3,550 = "Miguel Montañez" paid $3,550).
        if same_customer(q.customer_name, venta.customer):
            return True
        shared = _tokens(q.customer_name) & _tokens(venta.customer)
        return bool(shared) and Decimal(q.total or 0) == venta.total

    candidates = [
        q
        for q in db.query(Quote).filter(
            Quote.status.in_(("requested", "sent", "viewed", "needs_work", "accepted"))
        )
        if q.id not in taken and recent(q) and same_buyer(q)
    ]
    open_ones = [q for q in candidates if q.status != "accepted"]
    pool = open_ones or candidates
    return pool[0] if len(pool) == 1 else None


def apply_venta(
    db: Session,
    venta: ParsedVenta,
    *,
    user_email: str,
    sent_date: Optional[date] = None,
    link_quote: bool = True,
    dry_run: bool = False,
) -> dict:
    """Create or update the ledger row for this Venta. Returns {"action":
    "created"|"updated", "sale", "quote", "sheet_duplicates", "warnings"}."""
    from services.sales_sync import _customer_map, normalize_customer_name

    warnings = list(venta.warnings)
    current = (
        db.query(Sale)
        .filter(Sale.sheet_tab == SHEET_TAB, Sale.source_row == venta.source_row)
        .first()
    )
    today = datetime.now(timezone.utc).astimezone(BUSINESS_TZ).date()
    sale_day = (
        venta.sale_date
        or sent_date
        or (current.sale_date if current else None)
        or today
    )
    quote = find_quote(db, venta, sale_day, current) if link_quote else None
    sheet_dupes = (
        db.query(Sale)
        .filter(Sale.sheet_tab.like("VENTAS%"), Sale.folio == venta.folio)
        .all()
    )
    if sale_day < CUTOVER and sheet_dupes:
        warnings.append(
            f"Venta del {sale_day:%d/%m/%Y}, antes del corte {CUTOVER:%d/%m/%Y}, y ya "
            f"está en la hoja ({venta.folio}): se guarda el detalle sin sumarla dos veces."
        )
    result = {
        "action": "updated" if current else "created",
        "sale": None,
        "quote": quote,
        "sheet_duplicates": len(sheet_dupes),
        "sale_date": sale_day,
        "warnings": warnings,
    }
    if dry_run:
        return result

    now = datetime.now(timezone.utc)
    current_amount = current.amount if current is not None else None
    items = venta.items
    single = items[0] if len(items) == 1 else None
    payments = [
        {
            "label": p.label,
            "amount": str(p.amount),
            "date": p.date.isoformat() if p.date else None,
            "method": p.method,
        }
        for p in venta.payments
    ]
    methods = [p.method for p in venta.payments if p.method]
    customer_id = _customer_map(db).get(normalize_customer_name(venta.customer))
    fields = dict(
        sale_date=sale_day,
        month_label=None,
        customer_name=venta.customer[:200],
        customer_id=customer_id,
        description="; ".join(i.description for i in items) or None,
        unit=single.unit if single else None,
        quantity=single.quantity if single else None,
        unit_price=single.unit_price if single else None,
        amount=venta.total,
        payment_method=methods[-1] if methods else None,
        delivery_place=(venta.location or "")[:200] or None,
        reference=f"Venta {venta.label}",
        folio=venta.folio,
        paid_amount=venta.paid,
        pending_amount=pending_of(venta),
        payments=payments,
        imported_at=now,
        # Before the cutover the sheet already counts this sale.
        quarantined=sale_day < CUTOVER and bool(sheet_dupes),
        quarantine_reason=(
            "antes del corte: la venta ya cuenta desde la hoja"
            if sale_day < CUTOVER and sheet_dupes
            else None
        ),
    )
    stamp = f"{_stamp(now)} desde WhatsApp ({user_email})"
    if current is None:
        sale = Sale(sheet_tab=SHEET_TAB, source_row=venta.source_row, **fields)
        sale.notes = "\n".join(
            [f"NOTA: {n}" for n in venta.notes] + [f"[Registro] {stamp}"]
        )
        db.add(sale)
    else:
        sale = current
        for key, value in fields.items():
            setattr(sale, key, value)
        lines = [
            f"NOTA: {n}" for n in venta.notes if f"NOTA: {n}" not in (sale.notes or "")
        ]
        lines.append(f"[{venta.tag or 'Actualización'}] {stamp}")
        sale.notes = "\n".join(filter(None, [sale.notes, *lines]))

    if quote is not None:
        sale.quote_id = quote.id
        if quote.status != "accepted":
            quote.status = "accepted"
            quote.accepted_at = datetime.combine(
                sale_day, time(12, 0), tzinfo=BUSINESS_TZ
            )
        # A quote without products takes the sale's total when it had none
        # (WhatsApp-loaded quotes are $0) or the one a previous paste set.
        previous = current_amount
        if not quote.items and Decimal(quote.total or 0) in (Decimal("0"), previous):
            _set_flat_total(quote, venta.total)
        if f"Venta {venta.label}" not in (quote.notes or ""):
            _append_note(
                quote,
                f"[Venta] {_stamp(now)} Venta {venta.label} ${venta.total:,.2f} "
                f"({user_email})",
            )
        quote.updated_at = now

    if sale_day >= CUTOVER:
        for dupe in sheet_dupes:
            dupe.quarantined = True
            dupe.quarantine_reason = (
                f"duplicado: registrada desde WhatsApp (Venta {venta.label})"
            )
    db.commit()
    db.refresh(sale)
    result["sale"] = sale
    return result
