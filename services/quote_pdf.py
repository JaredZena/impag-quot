"""The customer quote PDF (COT-IMPAG-…pdf) as the source of a tracked quote.

Every quote the team sends is a PDF built from the same Word template:

    Asunto: Cotización 390526DGO      Fecha:16/05/2026
    En atención a: Edwin Santillano
    Ubicación: Santiago Papasquiaro, Durango.
    … CONCEPTO / UNIDAD / CANTIDAD / P. UNITARIO / IMPORTE …
    TOTAL
    $17,200.00
    Contexto: Reparación de un vivero en producción

so the PDF carries what the WhatsApp *Cotización Enviada* message lacks: the
total and the date. parse_quote_pdf() turns its text into the same
ParsedCotizacion services/quote_capture.py builds from the message, and the
PDF itself is kept in R2 as a file_metadata row named COT-IMPAG-{folio}-….pdf;
quote_files() finds it again by that name (no link column needed).
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from models import FileMetadata, Quote
from services.quote_capture import (
    QUOTE_PREFIX,
    CaptureError,
    ParsedCotizacion,
    parse_money,
)

PDF_CATEGORY = "cotizacion"
PDF_SUBTYPE = "cliente"

# "Cotización 390526DGO" / "COTIZACIÓN: 130426 DGO" (the Asunto line).
FOLIO_RE = re.compile(
    r"cotizaci[oó]n\s*(?:no\.?|n[uú]m(?:ero)?\.?|folio)?\s*:?\s*"
    r"(?P<digits>\d{6})[ ]?(?P<state>[A-Z]{2,4})?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# COT-IMPAG-390526DGO-EDWIN SANTILLANO-MALLASOMBRA 50%.pdf
FILENAME_RE = re.compile(
    r"COT-IMPAG-(?P<digits>\d{6})(?P<state>[A-Z]{2,4})?"
    r"(?:-(?:DGO|NAY|CHI|ZAC|SIN)(?=-))?"  # "621225NAY-DGO-…" double state
    r"(?:-(?P<cliente>[^-]+))?(?:-(?P<material>.+?))?\.(?:pdf|docx?)$",
    re.IGNORECASE,
)
FECHA_RE = re.compile(r"fecha\s*:?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", re.I)
ATENCION_RE = re.compile(r"(?:en\s+atenci[oó]n\s+a|cliente)\s*:\s*(.+)", re.I)
UBICACION_RE = re.compile(r"ubicaci[oó]n\s*:\s*(.+)", re.I)
MATERIAL_RE = re.compile(
    r"siguiente\s+cotizaci[oó]n\s+(?:de|para|del?)\s+(.+?)(?:\.\s|\.$|\n\s*\n)",
    re.I | re.S,
)
CONTEXTO_RE = re.compile(r"contexto\s*:\s*(.*?)(?:\n\s*nota\s*:|\Z)", re.I | re.S)
MONEY_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2}|\d{3,})")
# A line that is the grand-total label ("TOTAL", "Total:", "TOTAL CON IVA",
# "TOTAL NETO") — not SUBTOTAL, not "Total" inside a sentence.
TOTAL_LABEL_RE = re.compile(
    r"^\s*total(?:\s+(?:con\s+iva|neto|mxn|a\s+pagar|general))?\s*:?\s*(?P<rest>.*)$",
    re.I,
)


@dataclass
class PdfQuote:
    parsed: ParsedCotizacion
    fecha: Optional[date] = None
    contexto: Optional[str] = None


def _line(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip(" .,;:-")
    return value or None


def _total(lines: List[str]) -> Optional[Decimal]:
    """Amount after the last grand-total label (same line or the next few)."""
    found = None
    for i, raw in enumerate(lines):
        label = TOTAL_LABEL_RE.match(raw)
        if not label:
            continue
        window = [label.group("rest")] + lines[i + 1 : i + 5]
        for chunk in window:
            money = MONEY_RE.search(chunk or "")
            if money:
                found = parse_money(money.group(1))
                break
    return found


def parse_quote_pdf(text: str, filename: Optional[str] = None) -> PdfQuote:
    """Folio, client, place, date, total and context from the PDF's text.
    The folio comes from the Asunto line, else from the file name."""
    text = unicodedata.normalize("NFC", text or "")
    name = unicodedata.normalize("NFC", filename or "")
    lines = text.splitlines()
    from_name = FILENAME_RE.search(name)

    # The file name wins: it is what goes to the customer and the group, while
    # the Asunto line is often left over from the document it was copied from
    # (39 of 638 PDFs in R2 disagree, e.g. text 561224 in COT-IMPAG-561225…).
    folio = FOLIO_RE.search(text)
    if from_name:
        digits, state = from_name.group("digits"), (from_name.group("state") or "")
    elif folio:
        digits, state = folio.group("digits"), (folio.group("state") or "")
    else:
        raise CaptureError(
            "El PDF no trae folio «Cotización NNMMAA» ni se llama COT-IMPAG-NNMMAA…"
        )
    state = state.upper()
    if not state and folio and folio.group("digits") == digits:
        state = (folio.group("state") or "").upper()
    parsed = ParsedCotizacion(
        folio=digits + state,
        quote_number=f"{QUOTE_PREFIX}{digits}{state}",
        digits=digits,
    )
    if from_name and folio and folio.group("digits") != digits:
        parsed.warnings.append(
            f"El PDF dice «Cotización {folio.group('digits')}» pero se llama "
            f"{parsed.folio}: se usa el nombre del archivo."
        )

    atencion = ATENCION_RE.search(text)
    parsed.cliente = _line(atencion.group(1)) if atencion else None
    if not parsed.cliente and from_name and from_name.group("cliente"):
        parsed.cliente = _line(from_name.group("cliente")).title()
    ubicacion = UBICACION_RE.search(text)
    parsed.ubicacion = _line(ubicacion.group(1)) if ubicacion else None
    if from_name and from_name.group("material"):
        parsed.material = _line(from_name.group("material"))
    else:
        material = MATERIAL_RE.search(text)
        parsed.material = _line(material.group(1)) if material else None
    parsed.total = _total(lines)
    if parsed.total is None:
        parsed.warnings.append("No encontré el TOTAL en el PDF; captúralo a mano.")

    fecha = None
    match = FECHA_RE.search(text)
    if match:
        d, m, y = (int(x) for x in match.groups())
        try:
            fecha = date(y + 2000 if y < 100 else y, m, d)
        except ValueError:
            fecha = None
    contexto = CONTEXTO_RE.search(text)
    contexto = (
        _line(contexto.group(1))[:500]
        if contexto and _line(contexto.group(1))
        else None
    )

    if not parsed.cliente:
        raise CaptureError(f"El PDF {parsed.folio} no trae «En atención a:».")
    return PdfQuote(parsed=parsed, fecha=fecha, contexto=contexto)


def merge_with_message(pdf: PdfQuote, message: ParsedCotizacion) -> ParsedCotizacion:
    """PDF + the pasted WhatsApp message: the message's labels win (Hernán
    types them for the team: Entrega, Material/Proyecto, Actualización), the
    PDF fills the total and anything the message left out."""
    merged = message
    if message.digits != pdf.parsed.digits:
        merged.warnings.append(
            f"El mensaje dice {message.folio} y el PDF {pdf.parsed.folio}: "
            f"se usa el del mensaje."
        )
    if (
        not message.folio[6:]
        and pdf.parsed.folio[6:]
        and message.digits == pdf.parsed.digits
    ):
        merged.folio = pdf.parsed.folio
        merged.quote_number = pdf.parsed.quote_number
    merged.cliente = message.cliente or pdf.parsed.cliente
    merged.ubicacion = message.ubicacion or pdf.parsed.ubicacion
    merged.material = message.material or pdf.parsed.material
    if message.total is None:
        merged.total = pdf.parsed.total
    merged.warnings.extend(w for w in pdf.parsed.warnings if w not in merged.warnings)
    return merged


def _tokens(name: Optional[str]) -> set:
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return {t for t in re.findall(r"[a-z]{3,}", plain.lower())}


def quote_files(db: Session, quote: Quote) -> List[FileMetadata]:
    """PDFs stored for this quote's folio, newest first. Folios are reused now
    and then for another customer, so when the folio has files for more than
    one name, keep the ones that share a word with the quote's customer."""
    if not (quote.quote_number or "").startswith(QUOTE_PREFIX):
        return []
    digits = quote.quote_number[len(QUOTE_PREFIX) : len(QUOTE_PREFIX) + 6]
    if not digits.isdigit():
        return []
    rows = (
        db.query(FileMetadata)
        .filter(
            FileMetadata.original_filename.like(f"{QUOTE_PREFIX}{digits}%"),
            FileMetadata.content_type == "application/pdf",
            FileMetadata.archived_at.is_(None),
        )
        .order_by(FileMetadata.created_at.desc(), FileMetadata.id.desc())
        .all()
    )
    customer = _tokens(quote.customer_name)
    mine = [f for f in rows if customer & _tokens(f.original_filename)]
    return mine or rows


def stored_filename(parsed: ParsedCotizacion, uploaded_name: Optional[str]) -> str:
    """Keep the team's own COT-IMPAG-… name; otherwise prefix the folio so
    quote_files() can find it."""
    base = (uploaded_name or "cotizacion.pdf").rsplit("/", 1)[
        -1
    ].strip() or "cotizacion.pdf"
    if base.upper().startswith(f"{QUOTE_PREFIX}{parsed.digits}"):
        return base
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return f"{parsed.quote_number}-{base}"


def read_pdf(content: bytes, filename: Optional[str]) -> PdfQuote:
    from services.text_extraction import extract_text_from_pdf_bytes

    if not content.startswith(b"%PDF"):
        raise CaptureError("El archivo no es un PDF.")
    try:
        text = extract_text_from_pdf_bytes(content)
    except Exception as exc:  # fitz raises its own error types
        raise CaptureError(f"No pude leer el PDF ({exc}).") from exc
    return parse_quote_pdf(text, filename)


def attach_pdf(
    db: Session,
    quote: Quote,
    content: bytes,
    filename: Optional[str],
    *,
    user: dict,
    fecha: Optional[date] = None,
) -> FileMetadata:
    """Store the PDF in R2 under the quote's folio (same layout as
    routes/files.upload_file). The same file uploaded twice is not stored
    again."""
    from services.r2_storage import build_file_key, upload_file

    parsed = ParsedCotizacion(
        folio=quote.quote_number[len(QUOTE_PREFIX) :],
        quote_number=quote.quote_number,
        digits=quote.quote_number[len(QUOTE_PREFIX) : len(QUOTE_PREFIX) + 6],
    )
    name = stored_filename(parsed, filename)
    duplicate = (
        db.query(FileMetadata)
        .filter(
            FileMetadata.original_filename == name,
            FileMetadata.file_size_bytes == len(content),
            FileMetadata.archived_at.is_(None),
        )
        .first()
    )
    if duplicate:
        return duplicate

    row = FileMetadata(
        file_key="pending",
        original_filename=name,
        content_type="application/pdf",
        file_size_bytes=len(content),
        category=PDF_CATEGORY,
        subtype=PDF_SUBTYPE,
        document_date=fecha,
        description=f"Cotización {quote.quote_number} — {quote.customer_name}",
        uploaded_by_email=user.get("email", "unknown"),
        uploaded_by_name=user.get("name"),
    )
    db.add(row)
    db.flush()
    row.file_key = build_file_key(PDF_CATEGORY, row.id, name)
    try:
        upload_file(row.file_key, content, "application/pdf")
    except Exception:
        db.rollback()
        raise
    db.commit()
    db.refresh(row)
    return row
