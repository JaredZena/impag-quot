"""
Self-serve quotes from the todoparaelcampo.com.mx storefront.

- POST /storefront/quote-requests: the storefront's /api/quote-request function
  records a quote the buyer built in the "Cotizar" cart. Every line is priced
  by the storefront on its server from its catalog (the browser only sends
  handles and quantities). Returns the public quote link.
- POST /storefront/quotes/{access_token}/checkout: the storefront's
  /api/quote-checkout function asks for the amount to charge before it creates
  the Mercado Pago preference (external_reference = quote_number).

Server-to-server only, with the same dedicated key as /storefront/orders
(STOREFRONT_ORDERS_API_KEY). Logic: services/web_quotes.py. The payment itself
arrives through POST /storefront/orders like any web order.
"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from models import get_db
from routes.storefront_orders import (
    OrderAddress,
    OrderInvoice,
    Quantity,
    require_orders_key,
)
from services import web_quote_email
from services.web_orders import IVA_RATES
from services.web_quotes import (
    QuoteLimitReached,
    QuoteNotPayable,
    QuoteRequestRejected,
    create_quote_request,
    quote_checkout,
)

router = APIRouter(prefix="/storefront", tags=["storefront"])

UnitPrice = Annotated[Decimal, Field(ge=0, le=99_999_999, allow_inf_nan=False)]


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class QuoteCustomer(_Body):
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=1, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=300)


class QuoteDelivery(_Body):
    method: Literal["recoger", "paqueteria", "flete"]
    address: OrderAddress | None = None


class QuoteRequestItem(_Body):
    handle: str = Field(min_length=1, max_length=200)
    product_id: int | None = Field(default=None, ge=1)
    sku: str | None = Field(default=None, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    unit_label: str | None = Field(default=None, max_length=100)
    quantity: Quantity
    unit_price: UnitPrice  # before IVA; 0 = no published price (needs review)
    iva_rate: Decimal = Field(allow_inf_nan=False)

    @field_validator("iva_rate")
    @classmethod
    def _known_iva_rate(cls, value: Decimal) -> Decimal:
        if value not in IVA_RATES:
            raise ValueError("iva_rate must be 0 or 0.16")
        return value


class QuoteRequest(_Body):
    customer: QuoteCustomer
    delivery: QuoteDelivery
    invoice: OrderInvoice | None = None
    items: list[QuoteRequestItem] = Field(min_length=1, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/quote-requests", dependencies=[Depends(require_orders_key)])
def post_quote_request(
    body: QuoteRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Record a self-serve storefront quote; returns its public link."""
    try:
        result = create_quote_request(db, body)
    except QuoteRequestRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"message": "invalid quote request", "problems": exc.problems},
        )
    except QuoteLimitReached:
        db.rollback()
        raise HTTPException(
            status_code=429, detail={"message": "too many quote requests"}
        )
    except Exception:
        db.rollback()
        raise
    # Staff alert + the buyer's copy, after the commit (services/web_quote_email.py).
    web_quote_email.queue_created(db, result["quote_number"], background_tasks)
    return result


@router.post(
    "/quotes/{access_token}/checkout", dependencies=[Depends(require_orders_key)]
)
def post_quote_checkout(
    access_token: Annotated[
        str, Path(min_length=36, max_length=36, pattern=r"^[0-9a-f-]{36}$")
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """The amount and buyer for the Mercado Pago preference of a web quote."""
    try:
        return {"success": True, "data": quote_checkout(db, access_token)}
    except QuoteNotPayable as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason})
