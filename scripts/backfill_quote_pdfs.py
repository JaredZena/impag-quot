#!/usr/bin/env python3
"""Register the historical quote PDFs already in R2 (WhatsApp export imports)
as tracked quotes, with the total read from each PDF.

Source: file_metadata rows named COT-IMPAG-{folio}-{CLIENTE}-{MATERIAL}.pdf
(Dec 2025 – Jul 2026 when this was written). Each PDF goes through
services/quote_pdf.parse_quote_pdf, the same parser the admin uses when a PDF
is attached to a quote.

Rules:
- One quote per folio. Several PDFs of the same folio and customer are
  versions: the first one's date is the send date, the last one's total wins,
  and every version is listed in the notes.
- A folio reused for another customer (Hernán copied an old document) keeps
  the customer whose PDF says that folio in its Asunto line; the others are
  reported and skipped (quote_number is unique and <= 20 chars).
- "A QUIEN CORRESPONDA" price lists are skipped (no customer, not a deal).
- A folio already in the quote table is never changed, except that a $0
  total with no products is filled from the PDF.
- Older than the follow-up window (DEAD_AFTER_DAYS) -> "expired": the outcome
  is unknown and the vigencia is long gone; staff can still mark it Aceptada
  or Perdida. A sales note (NOT-IMPAG-…) for the same customer within 60 days
  is only mentioned in the notes as a lead ("Posible venta"), not trusted as
  a sale, because note folios are a separate numbering.
- The customer is linked when exactly one customer has the same name.

Usage:
    python scripts/backfill_quote_pdfs.py                 # dry-run
    python scripts/backfill_quote_pdfs.py --commit        # write
    python scripts/backfill_quote_pdfs.py --cache DIR     # reuse {file_id}.txt texts
"""

import os
import re
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import r2_bucket_name  # noqa: E402
from models import Customer, FileMetadata, Quote, SessionLocal  # noqa: E402
from services import quote_capture, quote_pdf  # noqa: E402
from services.quote_followup import DEAD_AFTER_DAYS  # noqa: E402

LOADED_BY = "backfill-pdf-2026-10"
NOTE_WINDOW_DAYS = 60


def _plain(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", text.lower())).strip()


def _tokens(text):
    return {t for t in _plain(text).split() if len(t) >= 3}


def _same_customer(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 2 or shared == ta or shared == tb


def _folio_month(digits):
    return date(2000 + int(digits[4:6]), int(digits[2:4]), 1)


def send_date(pdf, digits):
    """The PDF's Fecha when it is plausible for the folio's month (the date
    line is often left over from a copied document), else mid-month."""
    month = _folio_month(digits)
    if pdf.fecha and month - timedelta(days=7) <= pdf.fecha <= month + timedelta(
        days=50
    ):
        return pdf.fecha, False
    return month.replace(day=15), True


def load_texts(rows, cache):
    from services.r2_storage import get_r2_client
    from services.text_extraction import extract_text_from_pdf_bytes

    r2 = get_r2_client()

    def text_of(f):
        path = os.path.join(cache, f"{f.id}.txt") if cache else None
        if path and os.path.exists(path):
            return f.id, open(path).read()
        body = r2.get_object(Bucket=r2_bucket_name, Key=f.file_key)["Body"].read()
        text = extract_text_from_pdf_bytes(body)
        if path:
            open(path, "w").write(text)
        return f.id, text

    with ThreadPoolExecutor(8) as pool:
        return dict(pool.map(text_of, rows))


def sales_notes(db):
    """[(customer, month)] from NOT-IMPAG-NNMMYY…-CLIENTE files."""
    notes = []
    for (name,) in db.query(FileMetadata.original_filename).filter(
        FileMetadata.original_filename.like("NOT-IMPAG-%")
    ):
        m = re.match(r"NOT-IMPAG-(\d{6})[A-Z]*-([^-.]+)", name)
        if m and 1 <= int(m.group(1)[2:4]) <= 12:
            notes.append((m.group(2), _folio_month(m.group(1)), name.rsplit(".", 1)[0]))
    return notes


def main():
    commit = "--commit" in sys.argv
    cache = sys.argv[sys.argv.index("--cache") + 1] if "--cache" in sys.argv else None
    import urllib3

    urllib3.disable_warnings()  # get_r2_client uses verify=False

    db = SessionLocal()
    rows = (
        db.query(FileMetadata)
        .filter(
            FileMetadata.original_filename.like("COT-IMPAG-%"),
            FileMetadata.content_type == "application/pdf",
            FileMetadata.archived_at.is_(None),
        )
        .all()
    )
    texts = load_texts(rows, cache)
    notes = sales_notes(db)
    customers = defaultdict(list)
    for c in db.query(Customer.id, Customer.display_name):
        if c.display_name:
            customers[_plain(c.display_name)].append(c.id)

    by_digits = defaultdict(list)
    skipped = defaultdict(list)
    for f in rows:
        try:
            pdf = quote_pdf.parse_quote_pdf(texts[f.id], f.original_filename)
        except quote_capture.CaptureError as exc:
            skipped["sin folio/cliente"].append(f"{f.original_filename}: {exc}")
            continue
        if "QUIEN CORRESPONDA" in (pdf.parsed.cliente or "").upper() or (
            "QUIEN CORRESPONDA" in f.original_filename.upper()
        ):
            skipped["lista de precios"].append(pdf.parsed.folio)
            continue
        if not 1 <= int(pdf.parsed.digits[2:4]) <= 12:
            skipped["folio raro"].append(f.original_filename)
            continue
        by_digits[pdf.parsed.digits].append((f, pdf))

    created = filled = 0
    total_sum = Decimal(0)
    now = datetime.now(timezone.utc)
    for digits in sorted(by_digits, key=lambda d: (d[4:6], d[2:4], d[:2])):
        # Cluster the folio's PDFs by customer.
        clusters = []
        for f, pdf in by_digits[digits]:
            for cl in clusters:
                if _same_customer(cl[0][1].parsed.cliente, pdf.parsed.cliente):
                    cl.append((f, pdf))
                    break
            else:
                clusters.append([(f, pdf)])
        if len(clusters) > 1:
            # Keep the customer whose PDF text carries this folio.
            def owns(cl, digits=digits):
                return any(
                    re.search(rf"cotizaci[oó]n\s*:?\s*{digits}", texts[f.id], re.I)
                    for f, _ in cl
                )

            clusters.sort(
                key=lambda cl: (not owns(cl), min(p.fecha or date.max for _, p in cl))
            )
            for cl in clusters[1:]:
                skipped["folio repetido (otro cliente)"].append(
                    f"{cl[0][1].parsed.folio} {cl[0][1].parsed.cliente} "
                    f"(se queda {clusters[0][0][1].parsed.cliente})"
                )
        versions = sorted(clusters[0], key=lambda v: (v[1].fecha or date.min, v[0].id))
        first, last = versions[0][1], versions[-1][1]
        parsed = last.parsed
        sent_day, approx = send_date(first, digits)
        sent_at = datetime.combine(
            sent_day, time(12, 0), tzinfo=quote_capture.BUSINESS_TZ
        )

        existing = quote_capture.find_existing(db, parsed)
        if existing is not None:
            if (
                existing.items
                or Decimal(existing.total or 0) > 0
                or parsed.total is None
            ):
                skipped["ya registrada"].append(existing.quote_number)
                continue
            print(
                f"TOTAL    {existing.quote_number:22} ${parsed.total:>12,.2f}  (estaba en $0)"
            )
            filled += 1
            if commit:
                quote_capture._set_flat_total(existing, parsed.total)
                quote_capture._append_note(
                    existing,
                    f"[Total] {quote_capture._stamp(now)} ${parsed.total:,.2f} "
                    f"leído del PDF ({LOADED_BY})",
                )
                db.commit()
            continue

        status = "expired" if (now - sent_at).days > DEAD_AFTER_DAYS else "sent"
        lines = [f"Material/Proyecto: {parsed.material or '—'}"]
        if last.contexto:
            lines.append(f"Contexto: {last.contexto}")
        seen = set()
        for _, v in versions:
            key = (v.fecha, v.parsed.total)
            if len(versions) > 1 and key not in seen:
                seen.add(key)
                when = v.fecha.strftime("%d/%m/%Y") if v.fecha else "s/f"
                amount = (
                    f"${v.parsed.total:,.2f}"
                    if v.parsed.total is not None
                    else "sin total"
                )
                lines.append(f"[Versión] {when} {amount}")
        sale = next(
            (
                name
                for cust, month, name in notes
                if _same_customer(cust, parsed.cliente)
                and _folio_month(digits)
                <= month
                <= _folio_month(digits) + timedelta(days=NOTE_WINDOW_DAYS)
            ),
            None,
        )
        if sale:
            lines.append(
                f"Posible venta: {sale} (mismo cliente; confirmar y marcar Aceptada)"
            )
        lines.append(
            f"[Registro] {quote_capture._stamp(now)} desde PDF en R2"
            f"{' (fecha aproximada)' if approx else ''} ({LOADED_BY})"
        )
        matches = customers.get(_plain(parsed.cliente), [])

        print(
            f"NUEVA    {parsed.quote_number:22} {sent_day:%d/%m/%y}  "
            f"{(parsed.cliente or '')[:26]:26} {status:8} "
            f"{('$' + format(parsed.total, ',.2f')) if parsed.total is not None else 'sin total':>13}"
            f"{'  v' + str(len(versions)) if len(versions) > 1 else ''}"
            f"{'  ★' + sale if sale else ''}{'  👤' if len(matches) == 1 else ''}"
        )
        created += 1
        total_sum += parsed.total or 0
        if not commit:
            continue
        quote = Quote(
            quote_number=parsed.quote_number,
            status=status,
            customer_name=parsed.cliente[:200],
            customer_phone=quote_capture.NO_PHONE,
            customer_location=(parsed.ubicacion or "")[:300] or None,
            customer_id=matches[0] if len(matches) == 1 else None,
            validity_days=15,
            sent_at=sent_at,
            expired_at=sent_at + timedelta(days=15) if status == "expired" else None,
            created_by=LOADED_BY,
            notes="\n".join(lines),
        )
        quote_capture._set_flat_total(quote, parsed.total or Decimal("0"))
        db.add(quote)
        db.commit()
    db.close()

    print(f"\n{created} nuevas (${total_sum:,.2f}), {filled} totales llenados.")
    for reason, items in skipped.items():
        print(f"Omitidas — {reason}: {len(items)}")
        if reason not in ("ya registrada", "lista de precios"):
            for item in items[:15]:
                print(f"   {item}")
    if not commit:
        print("Dry-run — nada escrito. Re-ejecuta con --commit.")


if __name__ == "__main__":
    main()
