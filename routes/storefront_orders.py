"""
POST /storefront/orders: web orders from the todoparaelcampo.com.mx checkout.

Called server-to-server by the storefront's Vercel functions, never by a
browser (so no CORS change):
- event "checkout_created": best effort, when a checkout starts;
- event "payment_update": on every Mercado Pago payment webhook, after the
  function has verified MP's signature and re-read the payment from MP.

Authenticated with X-API-Key against a DEDICATED key, STOREFRONT_ORDERS_API_KEY
(not the sync key GitHub Actions holds), because this endpoint can mark orders
paid. Fail-closed: 503 while the key is unset, 401 on a missing or wrong key
(checked before the body is validated), 422 on an invalid body.

The recording logic lives in services/web_orders.py.
"""

import os
import secrets
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from models import get_db
from services.web_orders import IVA_RATES, OrderRejected, record_order

router = APIRouter(prefix="/storefront", tags=["storefront"])

# WEB-YYMMDD-XXXXXX; the storefront draws XXXXXX from an unambiguous A-Z/2-9
# alphabet. Accept that whole range so an alphabet tweak there can't drop orders.
REFERENCE_PATTERN = r"^WEB-[0-9]{6}-[A-Z2-9]{6}$"

# Every Mercado Pago payment status (services.web_orders.MP_STATUS_MAP).
MpStatus = Literal[
    "pending",
    "in_process",
    "authorized",
    "approved",
    "rejected",
    "cancelled",
    "refunded",
    "charged_back",
    "in_mediation",
]

Money = Annotated[Decimal, Field(ge=0, le=99_999_999, allow_inf_nan=False)]
Quantity = Annotated[Decimal, Field(gt=0, le=999_999, allow_inf_nan=False)]


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class OrderCustomer(_Body):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=300)


class OrderAddress(_Body):
    street: str | None = Field(default=None, max_length=200)
    number: str | None = Field(default=None, max_length=50)
    colonia: str | None = Field(default=None, max_length=200)
    cp: str | None = Field(default=None, max_length=10)
    municipio: str | None = Field(default=None, max_length=200)
    estado: str | None = Field(default=None, max_length=100)
    references: str | None = Field(default=None, max_length=500)


class OrderDelivery(_Body):
    method: Literal["recoger", "paqueteria", "flete"]
    address: OrderAddress | None = None
    cost_total: Money = Decimal(0)  # IVA included


class OrderInvoice(_Body):
    requires_invoice: bool = True
    rfc: str | None = Field(default=None, max_length=20)
    razon_social: str | None = Field(default=None, max_length=300)
    regimen_fiscal: str | None = Field(default=None, max_length=10)
    cp_fiscal: str | None = Field(default=None, max_length=10)
    uso_cfdi: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=255)


class OrderItem(_Body):
    handle: str = Field(min_length=1, max_length=200)
    product_id: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=1, max_length=500)
    unit_label: str | None = Field(default=None, max_length=100)
    quantity: Quantity
    unit_price: Money  # before IVA
    iva_rate: Decimal = Field(allow_inf_nan=False)
    unit_total: Money  # IVA included

    @field_validator("iva_rate")
    @classmethod
    def _known_iva_rate(cls, value: Decimal) -> Decimal:
        if value not in IVA_RATES:
            raise ValueError("iva_rate must be 0 or 0.16")
        return value


class OrderTotals(_Body):
    subtotal: Money
    iva_amount: Money
    total: Money
    currency: Literal["MXN"]


class OrderMercadoPago(_Body):
    preference_id: str | None = Field(default=None, max_length=255)
    payment_id: str | None = Field(default=None, max_length=64)
    status: MpStatus | None = None
    status_detail: str | None = Field(default=None, max_length=100)
    payment_type_id: str | None = Field(default=None, max_length=40)
    payment_method_id: str | None = Field(default=None, max_length=40)
    transaction_amount: Money | None = None
    date_approved: str | None = Field(default=None, max_length=64)
    live_mode: bool | None = None

    @field_validator("preference_id", "payment_id", mode="before")
    @classmethod
    def _ids_as_text(cls, value):
        # Mercado Pago sends ids as JSON numbers; keep them as text.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class StorefrontOrder(_Body):
    event: Literal["checkout_created", "payment_update"]
    external_reference: str = Field(pattern=REFERENCE_PATTERN)
    customer: OrderCustomer
    delivery: OrderDelivery
    invoice: OrderInvoice | None = None
    items: list[OrderItem] = Field(min_length=1, max_length=50)
    totals: OrderTotals
    mercadopago: OrderMercadoPago | None = None

    @model_validator(mode="after")
    def _payment_update_has_payment(self):
        if self.event == "payment_update":
            mp = self.mercadopago
            if mp is None or not mp.payment_id or not mp.status:
                raise ValueError(
                    "payment_update needs mercadopago.payment_id and mercadopago.status"
                )
        return self


def require_orders_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = os.getenv("STOREFRONT_ORDERS_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503, detail="STOREFRONT_ORDERS_API_KEY not configured"
        )
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
    # which would turn a garbage header into a 500 instead of a 401.
    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8", "replace"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.post("/orders", dependencies=[Depends(require_orders_key)])
def post_storefront_order(
    order: StorefrontOrder, db: Annotated[Session, Depends(get_db)]
):
    """Record a storefront checkout or Mercado Pago payment event (idempotent)."""
    try:
        return record_order(db, order)
    except OrderRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"message": "invalid order", "problems": exc.problems},
        )
    except Exception:
        db.rollback()
        raise
