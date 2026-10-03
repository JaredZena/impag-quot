"""
Cotizador solar of the todoparaelcampo.com.mx storefront.

POST /storefront/solar-quotes: the storefront's /api/solar-quote function
sends the buyer's CFE bill (base64 PDF or photos), the system they picked,
their answers, their contact, and the priced catalog candidates for that
system. Claude reads the bill, the backend sizes the system, Claude picks a
kit and explains it, and the quote is recorded as a self-serve web quote.
Logic: services/solar_quotes.py.

Server-to-server only, with the same dedicated key as /storefront/orders
(STOREFRONT_ORDERS_API_KEY).
"""

import base64
import binascii
import logging
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from models import get_db
from routes.storefront_orders import require_orders_key
from routes.storefront_quotes import QuoteCustomer, UnitPrice
from services import solar_quotes, web_quote_email
from services.web_orders import IVA_RATES
from services.web_quotes import QuoteLimitReached, QuoteRequestRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storefront", tags=["storefront"])

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
FILE_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
# Magic bytes: the declared type must match the content Claude will read.
MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class SolarFile(_Body):
    media_type: Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
    data: str = Field(min_length=8, max_length=MAX_FILE_BYTES * 4 // 3 + 8)

    def decoded(self) -> bytes:
        try:
            content = base64.b64decode(self.data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("file_not_base64")
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("file_too_large")
        if not content.startswith(MAGIC[self.media_type]):
            raise ValueError("file_type_mismatch")
        return content


class SolarSpecs(_Body):
    kind: Literal["aislado", "pozo", "superficie"]
    daily_wh: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    peak_w: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    head_m: Decimal | None = Field(default=None, ge=0, le=2_000)
    flow_lpm: Decimal | None = Field(default=None, ge=0, le=100_000)
    hp: Decimal | None = Field(default=None, ge=0, le=1_000)


class SolarCandidate(_Body):
    handle: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    product_id: int | None = Field(default=None, ge=1)
    unit_label: str | None = Field(default=None, max_length=100)
    price: UnitPrice
    iva_rate: Decimal = Field(allow_inf_nan=False)
    specs: SolarSpecs

    @field_validator("iva_rate")
    @classmethod
    def _known_iva_rate(cls, value: Decimal) -> Decimal:
        if value not in IVA_RATES:
            raise ValueError("iva_rate must be 0 or 0.16")
        return value

    @field_validator("price")
    @classmethod
    def _priced(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("candidates must have a price")
        return value


class SolarAnswers(_Body):
    source: Literal["pozo", "superficie"] | None = None
    depth_m: Decimal | None = Field(default=None, ge=0, le=1_500)
    lift_m: Decimal | None = Field(default=None, ge=0, le=500)
    daily_liters: int | None = Field(default=None, ge=0, le=50_000_000)
    usage: str | None = Field(default=None, max_length=500)


class SolarQuoteRequest(_Body):
    system: Literal["interconectado", "aislado", "bombeo"]
    files: list[SolarFile] = Field(default_factory=list, max_length=2)
    answers: SolarAnswers = Field(default_factory=SolarAnswers)
    customer: QuoteCustomer
    candidates: list[SolarCandidate] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _rules(self):
        if self.system != "bombeo" and not self.files:
            raise ValueError("a CFE bill is required for this system")
        if self.system == "bombeo" and not self.answers.depth_m:
            raise ValueError("bombeo needs depth_m")
        if self.system != "interconectado" and not self.candidates:
            raise ValueError("candidates are required for this system")
        return self


def _problem(status: int, reason: str):
    return HTTPException(status_code=status, detail={"reason": reason})


@router.post("/solar-quotes", dependencies=[Depends(require_orders_key)])
def post_solar_quote(
    body: SolarQuoteRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Read the bill, size the system and record the quote; returns the public
    link plus what the storefront shows (bill summary, sizing, explanation)."""
    files: list[tuple[bytes, str]] = []
    for item in body.files:
        try:
            files.append((item.decoded(), item.media_type))
        except ValueError as exc:
            raise _problem(422, str(exc))
    if sum(len(content) for content, _ in files) > MAX_TOTAL_BYTES:
        raise _problem(422, "file_too_large")

    try:
        solar_quotes.take_ai_slot()
        result = solar_quotes.create_solar_quote(
            db,
            system=body.system,
            files=files,
            answers=body.answers.model_dump(mode="json"),
            customer=body.customer,
            candidates=[c.model_dump(mode="json") for c in body.candidates],
        )
    except solar_quotes.BillUnreadable:
        db.rollback()
        raise _problem(422, "bill_unreadable")
    except solar_quotes.SolarLimitReached:
        db.rollback()
        raise _problem(429, "too_many_requests")
    except QuoteLimitReached:
        db.rollback()
        raise _problem(429, "too_many_requests")
    except QuoteRequestRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"reason": "invalid_request", "problems": exc.problems},
        )
    except solar_quotes.SolarUnavailable as exc:
        db.rollback()
        status = 503 if exc.reason in ("no_api_key", "no_candidates") else 502
        raise _problem(status, exc.reason)
    except Exception:
        db.rollback()
        raise

    attachments = [
        {
            "filename": f"recibo-cfe-{result['quote_number']}-{i + 1}.{FILE_EXTENSIONS[media_type]}",
            "content": content,
            "content_type": media_type,
        }
        for i, (content, media_type) in enumerate(files)
    ]
    web_quote_email.queue_created(
        db, result["quote_number"], background_tasks, attachments=attachments
    )
    return result
