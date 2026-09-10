"""
POST /storefront/orders: web orders from the todoparaelcampo.com.mx checkout.

The storefront's Vercel functions call it server-to-server, never a browser
(so no CORS change). Two events:
- "checkout_created" is best effort, sent when a checkout starts. It always
  carries the whole order and records an unpaid draft.
- "payment_update" is sent on every Mercado Pago payment webhook, after the
  function has verified MP's signature and re-read the payment from MP. It
  carries the order rebuilt from the payment's metadata when the storefront
  could rebuild it. Otherwise customer, delivery and totals are null and items
  is [] (DESIGN §7: external_reference is the join key), and the backend works
  from the draft it stored at checkout.

A payment_update is never refused over its order data, because the buyer may
already have paid. An unusable order part is dropped with a warning and the
stored draft is used instead. Only a broken envelope (reference, event, or the
Mercado Pago block) gets a 422.

Authenticated with X-API-Key against a DEDICATED key,
STOREFRONT_ORDERS_API_KEY, not the sync key GitHub Actions holds, because this
endpoint can mark orders paid. It fails closed:
- 503 while the key is unset;
- 401 on a missing or wrong key, checked before the body is validated;
- 422 on an invalid body;
- 429 when checkout_created would exceed WEB_ORDERS_MAX_DRAFTS_PER_HOUR
  unpaid drafts.

Other env vars, read per request (see CLAUDE.md): WEB_ORDER_NOTIFY_EMAILS,
WEB_ORDER_ASSIGNEE, WEB_ORDERS_ALLOW_TEST and WEB_ORDERS_MAX_DRAFTS_PER_HOUR.
The buyer confirmation email also needs RESEND_API_KEY and
WEB_ORDER_STORE_ADDRESS, and optionally takes WEB_ORDER_RETURN_ADDRESS and
WEB_ORDER_FROM_EMAIL.

The recording logic lives in services/web_orders.py.
"""

import os
import secrets
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from models import get_db
from services.web_order_email import send_buyer_confirmation
from services.web_orders import (
    IVA_RATES,
    DraftLimitReached,
    OrderRejected,
    record_order,
)

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

# The order part of a payload: optional on a payment_update, and dropped (with a
# warning) when it doesn't validate there.
ORDER_PARTS = ("customer", "delivery", "invoice", "items", "totals")


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
    """One storefront event (DESIGN §7).

    checkout_created always carries the whole order. A payment_update may leave
    customer, delivery and totals null and items [], when the storefront could
    not rebuild the order from the payment's metadata. The backend then uses
    the draft it stored at checkout.
    """

    event: Literal["checkout_created", "payment_update"]
    external_reference: str = Field(pattern=REFERENCE_PATTERN)
    customer: OrderCustomer | None = None
    delivery: OrderDelivery | None = None
    invoice: OrderInvoice | None = None
    items: list[OrderItem] = Field(default_factory=list, max_length=50)
    totals: OrderTotals | None = None
    mercadopago: OrderMercadoPago | None = None

    # Set by parse_order when it had to drop part of a payment_update.
    _parse_warnings: list[str] = PrivateAttr(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _no_items_is_empty(cls, value):
        return [] if value is None else value

    @property
    def has_order_data(self) -> bool:
        """True when the event carries a whole order (customer, delivery,
        totals and at least one item)."""
        return (
            self.customer is not None
            and self.delivery is not None
            and self.totals is not None
            and bool(self.items)
        )

    @property
    def parse_warnings(self) -> list[str]:
        return list(self._parse_warnings)

    @model_validator(mode="after")
    def _event_requirements(self):
        if self.event == "checkout_created":
            if not self.has_order_data:
                raise ValueError(
                    "checkout_created needs customer, delivery, items and totals"
                )
        else:
            mp = self.mercadopago
            if mp is None or not mp.payment_id or not mp.status:
                raise ValueError(
                    "payment_update needs mercadopago.payment_id and mercadopago.status"
                )
        return self


def parse_order(body: Any) -> StorefrontOrder:
    """Validate a request body.

    A payment_update whose order part is partly unusable is still recorded,
    because the buyer may already have paid. The failing parts are dropped and
    named in a warning, and the backend falls back to the stored draft for
    them. Anything else invalid gets the usual 422.
    """
    try:
        return StorefrontOrder.model_validate(body)
    except ValidationError as exc:
        salvaged = _salvage_payment(body, exc)
        if salvaged is not None:
            return salvaged
        raise RequestValidationError(
            [
                {**err, "loc": ("body", *err["loc"])}
                for err in exc.errors(include_url=False)
            ]
        ) from exc


def _salvage_payment(body: Any, exc: ValidationError) -> StorefrontOrder | None:
    if not isinstance(body, dict) or body.get("event") != "payment_update":
        return None
    failing = {err["loc"][0] if err["loc"] else None for err in exc.errors()}
    if not failing or not failing <= set(ORDER_PARTS):
        return None  # the envelope itself is broken: a real 422
    kept = {key: value for key, value in body.items() if key not in failing}
    try:
        order = StorefrontOrder.model_validate(kept)
    except ValidationError:
        return None
    order._parse_warnings = [f"order_data_invalid:{','.join(sorted(failing))}"]
    return order


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
    body: Annotated[Any, Body(description="A StorefrontOrder (see this module).")],
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    """Record a storefront checkout or Mercado Pago payment event (idempotent)."""
    order = parse_order(body)
    outbox: list[dict] = []
    try:
        result = record_order(db, order, outbox=outbox)
    except OrderRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"message": "invalid order", "problems": exc.problems},
        )
    except DraftLimitReached as exc:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail={
                "message": "too many unpaid web drafts in the last hour",
                "limit": exc.limit,
            },
        )
    except Exception:
        db.rollback()
        raise
    # After the commit, in the background: a slow email provider must never
    # delay the Mercado Pago webhook.
    for message in outbox:
        background_tasks.add_task(send_buyer_confirmation, message)
    return result
