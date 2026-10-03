"""
Cotizador solar of the todoparaelcampo.com.mx storefront (/cotizador-solar).

The buyer uploads a CFE bill (a PDF or one or two photos), picks a system
(interconectado, aislado or bombeo solar) and answers a few questions. The
storefront's /api/solar-quote function adds the priced catalog candidates
(handle, title, pre-IVA price, IVA, backend id, specs from
src/data/solar-systems.json) and calls POST /storefront/solar-quotes
(routes/storefront_solar.py), which runs, in order:

1. read_bill(): Claude reads the bill into BILL_SCHEMA (tarifa, kWh of the
   period, the history table, importe, municipio/estado).
2. size(): deterministic sizing.
   - interconectado: kWp from the average daily kWh and the state's peak sun
     hours, rounded up to whole panels. Price = SOLAR_INTERCONECTADO_BASE +
     SOLAR_INTERCONECTADO_PER_KW × kWp, installed, + IVA: the owner's fit to
     the Oct-2025 quotes (5 kW $78k, 6 kW $87k), decided 2026-10-03.
   - aislado: the storefront's off-grid kits by the Wh/day each one carries.
   - bombeo: the pump kits whose head covers the well (and whose flow covers
     the daily water, when the buyer gives it).
3. advise(): Claude picks one kit among the feasible ones (an enum, so it
   cannot pick anything else) and writes the buyer-facing explanation from the
   computed numbers. If that call fails, a plain template explanation and the
   default pick are used: the quote never depends on the second call.
4. The quote is recorded by services/web_quotes.create_quote_request as a
   self-serve web quote (payable at once with Mercado Pago, the owner's call
   for all three systems) carrying block["solar"] for staff.

It goes to review instead (draft; staff confirm and press Enviar, the same
link then shows the pay button) when the formula or the catalog does not
cover the case: no kit fits, a surface pump without published head/flow, an
interconectado system above SOLAR_MAX_INSTANT_KWP, a medium-voltage tariff,
or an installation outside Durango (travel is quoted by hand).
"""

import base64
import json
import logging
import math
import os
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from config import claude_api_key
from services.web_quotes import REVIEW_LABELS, create_quote_request

logger = logging.getLogger(__name__)

SYSTEMS = ("interconectado", "aislado", "bombeo")
SYSTEM_NAMES = {
    "interconectado": "Sistema solar interconectado",
    "aislado": "Sistema solar aislado",
    "bombeo": "Bombeo solar",
}

DEFAULT_MODEL = "claude-opus-5"
READ_TIMEOUT_S = 30.0
ADVICE_TIMEOUT_S = 20.0
FALLBACK_BETA = "server-side-fallback-2026-07-01"

PERFORMANCE_RATIO = 0.78  # inverter, wiring, heat and dirt losses
PUMP_SUN_HOURS = 6  # hours a day a solar pump runs near its nominal flow
HEAD_MARGIN = 1.10  # friction and fittings on top of the static head
KIT_HEAD_USE = 0.85  # a kit's "pozo de N m" is its max head; plan at 85 %
NET_METERING_SAVING = 0.90  # the minimum charges stay on the bill
DEFAULT_HSP = 5.4
MEDIUM_VOLTAGE_TARIFFS = ("GDMTO", "GDMTH", "DIST", "DIT", "RAMT", "GDMT")

# Average peak sun hours (kWh/m²/day) by state, rounded: the planning figure
# installers use for a fixed tilted array.
HSP_BY_STATE = {
    "aguascalientes": 5.7,
    "baja california": 5.9,
    "baja california sur": 6.0,
    "campeche": 5.1,
    "chiapas": 4.9,
    "chihuahua": 5.9,
    "ciudad de mexico": 5.2,
    "coahuila": 5.6,
    "colima": 5.5,
    "durango": 5.8,
    "estado de mexico": 5.3,
    "guanajuato": 5.6,
    "guerrero": 5.5,
    "hidalgo": 5.2,
    "jalisco": 5.6,
    "michoacan": 5.5,
    "morelos": 5.5,
    "nayarit": 5.5,
    "nuevo leon": 5.2,
    "oaxaca": 5.4,
    "puebla": 5.3,
    "queretaro": 5.5,
    "quintana roo": 5.1,
    "san luis potosi": 5.5,
    "sinaloa": 5.7,
    "sonora": 6.1,
    "tabasco": 4.6,
    "tamaulipas": 5.1,
    "tlaxcala": 5.3,
    "veracruz": 4.8,
    "yucatan": 5.2,
    "zacatecas": 5.8,
}
STATE_ALIASES = {
    "cdmx": "ciudad de mexico",
    "distrito federal": "ciudad de mexico",
    "df": "ciudad de mexico",
    "mexico": "estado de mexico",
    "edomex": "estado de mexico",
    "dgo": "durango",
    "chih": "chihuahua",
    "zac": "zacatecas",
    "coah": "coahuila",
    "sin": "sinaloa",
    "son": "sonora",
    "jal": "jalisco",
    "ags": "aguascalientes",
    "nl": "nuevo leon",
    "slp": "san luis potosi",
    "qro": "queretaro",
    "gto": "guanajuato",
    "mich": "michoacan",
    "coahuila de zaragoza": "coahuila",
    "michoacan de ocampo": "michoacan",
    "veracruz de ignacio de la llave": "veracruz",
}
HOME_STATE = "durango"


class SolarUnavailable(Exception):
    """No Claude key, or Claude could not read the bill (HTTP 503/502)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BillUnreadable(Exception):
    """The upload is not a readable CFE bill (HTTP 422)."""


class SolarLimitReached(Exception):
    pass


# ── settings ─────────────────────────────────────────────────────────────────


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def model() -> str:
    return os.getenv("SOLAR_QUOTE_MODEL", "").strip() or DEFAULT_MODEL


def interconectado_base() -> float:
    return _env_number("SOLAR_INTERCONECTADO_BASE", 33_000)


def interconectado_per_kw() -> float:
    return _env_number("SOLAR_INTERCONECTADO_PER_KW", 9_000)


def panel_w() -> int:
    return int(_env_number("SOLAR_PANEL_W", 620))


def max_instant_kwp() -> float:
    return _env_number("SOLAR_MAX_INSTANT_KWP", 30)


# ── abuse cap ────────────────────────────────────────────────────────────────

_ai_calls: deque[float] = deque()
_ai_lock = threading.Lock()


def take_ai_slot(now: float | None = None) -> None:
    """Every request reads a bill with Claude before any quote exists, so the
    quote caps in web_quotes do not cover a flood of junk uploads. Per
    instance: SOLAR_AI_MAX_PER_HOUR (default 40)."""
    limit = int(_env_number("SOLAR_AI_MAX_PER_HOUR", 40))
    now = time.monotonic() if now is None else now
    with _ai_lock:
        while _ai_calls and now - _ai_calls[0] > 3600:
            _ai_calls.popleft()
        if len(_ai_calls) >= limit:
            raise SolarLimitReached()
        _ai_calls.append(now)


# ── Claude ───────────────────────────────────────────────────────────────────


def _client() -> anthropic.Anthropic:
    if not claude_api_key:
        raise SolarUnavailable("no_api_key")
    return anthropic.Anthropic(api_key=claude_api_key, max_retries=1)


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


BILL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "es_recibo_cfe": {"type": "boolean"},
        "calidad": {"type": "string", "enum": ["completa", "parcial", "ilegible"]},
        "tarifa": _nullable({"type": "string"}),
        "periodo": _nullable({"type": "string", "enum": ["bimestral", "mensual"]}),
        "periodo_inicio": _nullable({"type": "string", "format": "date"}),
        "periodo_fin": _nullable({"type": "string", "format": "date"}),
        "consumo_kwh": _nullable({"type": "number"}),
        "importe_periodo": _nullable({"type": "number"}),
        "total_a_pagar": _nullable({"type": "number"}),
        "cargo_fijo": _nullable({"type": "number"}),
        "historial": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "periodo": {"type": "string"},
                    "kwh": {"type": "number"},
                    "importe": _nullable({"type": "number"}),
                },
                "required": ["periodo", "kwh", "importe"],
                "additionalProperties": False,
            },
        },
        "demanda_kw": _nullable({"type": "number"}),
        "numero_servicio": _nullable({"type": "string"}),
        "titular": _nullable({"type": "string"}),
        "municipio": _nullable({"type": "string"}),
        "estado": _nullable({"type": "string"}),
        "codigo_postal": _nullable({"type": "string"}),
        "observaciones": _nullable({"type": "string"}),
    },
    "required": [
        "es_recibo_cfe",
        "calidad",
        "tarifa",
        "periodo",
        "periodo_inicio",
        "periodo_fin",
        "consumo_kwh",
        "importe_periodo",
        "total_a_pagar",
        "cargo_fijo",
        "historial",
        "demanda_kw",
        "numero_servicio",
        "titular",
        "municipio",
        "estado",
        "codigo_postal",
        "observaciones",
    ],
    "additionalProperties": False,
}

READ_PROMPT = """Lee este recibo de luz de CFE (México) y llena el esquema.

- es_recibo_cfe: false si el archivo no es un aviso-recibo de CFE.
- calidad: "completa" si leíste consumo y periodo; "parcial" si falta algo importante; "ilegible" si no se puede leer.
- tarifa: tal como aparece (1, 1A…1F, DAC, PDBT, GDBT, GDMTO, GDMTH, 9, 9M, 9CU, 9N, RABT, RAMT…).
- periodo: "bimestral" si el periodo facturado abarca unos dos meses, "mensual" si uno.
- consumo_kwh: la energía total del periodo actual en kWh (suma básico + intermedio + excedente si viene desglosado).
- importe_periodo: lo facturado por este periodo con IVA ("Fac. del Periodo" o el total del periodo), SIN adeudo anterior, pagos ni DAP. total_a_pagar: el "TOTAL A PAGAR" impreso. cargo_fijo: el cargo fijo o de suministro del periodo sin IVA, si aparece (tarifas PDBT, GDBT y similares); null en tarifas domésticas sin cargo fijo.
- historial: cada periodo de la tabla o gráfica de consumo histórico que puedas leer, del más reciente al más antiguo, con su kWh y su importe si aparece. Incluye el periodo actual. No inventes valores que no se lean.
- estado: nombre completo del estado de la dirección del servicio (p. ej. "Durango"); municipio igual.
- observaciones: algo que un ingeniero solar deba saber (p. ej. lecturas estimadas, consumo muy bajo o atípico), o null.
Usa null en lo que no aparezca."""


def _content_blocks(files: list[tuple[bytes, str]]) -> list[dict]:
    blocks: list[dict] = []
    for content, media_type in files:
        data = base64.standard_b64encode(content).decode("ascii")
        kind = "document" if media_type == "application/pdf" else "image"
        blocks.append(
            {
                "type": kind,
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }
        )
    return blocks


def _structured_call(
    content: list[dict], schema: dict, effort: str, timeout: float, max_tokens: int
) -> dict:
    """One Claude request whose text answer is JSON matching `schema`."""
    client = _client()
    try:
        response = client.with_options(timeout=timeout).beta.messages.create(
            model=model(),
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIStatusError as exc:
        logger.error("solar quote: Claude HTTP %s", exc.status_code)
        raise SolarUnavailable("ai_error") from exc
    except anthropic.APIConnectionError as exc:  # includes timeouts
        logger.error("solar quote: Claude unreachable: %s", type(exc).__name__)
        raise SolarUnavailable("ai_unreachable") from exc
    if response.stop_reason in ("refusal", "max_tokens"):
        logger.error("solar quote: Claude stopped with %s", response.stop_reason)
        raise SolarUnavailable(f"ai_{response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SolarUnavailable("ai_bad_json") from exc
    if not isinstance(data, dict):
        raise SolarUnavailable("ai_bad_json")
    return data


def read_bill(files: list[tuple[bytes, str]]) -> dict:
    """The bill's facts (BILL_SCHEMA), or BillUnreadable / SolarUnavailable."""
    data = _structured_call(
        _content_blocks(files) + [{"type": "text", "text": READ_PROMPT}],
        BILL_SCHEMA,
        effort="medium",
        timeout=READ_TIMEOUT_S,
        max_tokens=8000,
    )
    if not data.get("es_recibo_cfe") or data.get("calidad") == "ilegible":
        raise BillUnreadable()
    if not _positive(data.get("consumo_kwh")) and not _history_kwh(data):
        raise BillUnreadable()
    return data


# ── bill arithmetic ──────────────────────────────────────────────────────────


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and math.isfinite(number) else None


def _history_kwh(bill: dict) -> list[float]:
    rows = bill.get("historial") or []
    return [
        v for v in (_positive(r.get("kwh")) for r in rows if isinstance(r, dict)) if v
    ]


def _history_importe(bill: dict) -> list[float]:
    rows = bill.get("historial") or []
    return [
        v
        for v in (_positive(r.get("importe")) for r in rows if isinstance(r, dict))
        if v
    ]


def _norm(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(raw.lower().replace(".", " ").replace(",", " ").split())


def state_key(text: str | None) -> str | None:
    """'DGO.', 'Durango', 'Nuevo Ideal, Dgo' → 'durango' (None when unknown)."""
    norm = _norm(text)
    if not norm:
        return None
    if norm in HSP_BY_STATE:
        return norm
    if norm in STATE_ALIASES:
        return STATE_ALIASES[norm]
    # Free text such as "Canatlán, Durango": the longest state name it ends with.
    for name in sorted(HSP_BY_STATE, key=len, reverse=True):
        if norm.endswith(name):
            return name
    last = norm.split(" ")[-1]
    return STATE_ALIASES.get(last)


@dataclass
class Usage:
    """What the bill says about consumption, normalised to a month."""

    periodo: str  # "bimestral" | "mensual"
    periods_per_year: int
    avg_kwh_period: float
    daily_kwh: float
    monthly_kwh: float
    avg_importe_period: float | None
    periods_read: int
    tarifa: str | None
    state: str | None
    # The fixed/supply charge with IVA: solar does not remove it.
    fixed_charge_period: float | None = None


def _days_between(start: Any, end: Any) -> int | None:
    try:
        days = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except (TypeError, ValueError):
        return None
    return days if 20 <= days <= 75 else None


def usage_from_bill(bill: dict | None, fallback_state: str | None) -> Usage | None:
    if not bill:
        return None
    periodo = bill.get("periodo") or "bimestral"
    if periodo not in ("bimestral", "mensual"):
        periodo = "bimestral"
    days_billed = _days_between(bill.get("periodo_inicio"), bill.get("periodo_fin"))
    if days_billed:  # the printed dates beat the model's reading of the layout
        periodo = "bimestral" if days_billed > 45 else "mensual"
    per_year = 6 if periodo == "bimestral" else 12
    days = 365 / per_year
    history = _history_kwh(bill)[:per_year]  # the last year at most
    current = _positive(bill.get("consumo_kwh"))
    values = history or ([current] if current else [])
    if not values:
        return None
    avg = sum(values) / len(values)
    importes = _history_importe(bill)[:per_year]
    importe_now = _positive(bill.get("importe_periodo"))
    avg_importe = (
        sum(importes) / len(importes)
        if len(importes) >= 2
        else (importe_now or (importes[0] if importes else None))
    )
    tarifa = (bill.get("tarifa") or "").strip().upper() or None
    fixed = _positive(bill.get("cargo_fijo"))
    return Usage(
        periodo=periodo,
        periods_per_year=per_year,
        avg_kwh_period=round(avg, 1),
        daily_kwh=round(avg / days, 2),
        monthly_kwh=round(avg * per_year / 12, 1),
        avg_importe_period=round(avg_importe, 2) if avg_importe else None,
        periods_read=len(values),
        tarifa=tarifa,
        state=state_key(bill.get("estado")) or state_key(fallback_state),
        fixed_charge_period=round(fixed * 1.16, 2) if fixed else None,
    )


# ── sizing ───────────────────────────────────────────────────────────────────


@dataclass
class Sizing:
    system: str
    lines: list[dict] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    # aislado/bombeo: the kits Claude may pick from, and the default pick.
    choices: list[dict] = field(default_factory=list)
    default_handle: str | None = None


def _round100(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("1E2")))


def _hsp(state: str | None) -> float:
    return HSP_BY_STATE.get(state or "", DEFAULT_HSP)


def size_interconectado(usage: Usage | None) -> Sizing:
    sizing = Sizing("interconectado")
    if usage is None:
        raise BillUnreadable()
    hsp = _hsp(usage.state)
    kwp_needed = usage.daily_kwh / (hsp * PERFORMANCE_RATIO)
    watts = panel_w()
    panels = max(2, math.ceil(kwp_needed * 1000 / watts))
    kwp = round(panels * watts / 1000, 2)
    price = _round100(interconectado_base() + interconectado_per_kw() * kwp)
    annual_kwh = usage.avg_kwh_period * usage.periods_per_year
    annual_gen = kwp * hsp * PERFORMANCE_RATIO * 365
    coverage = min(1.0, annual_gen / annual_kwh) if annual_kwh else 1.0
    annual_bill = (
        usage.avg_importe_period * usage.periods_per_year
        if usage.avg_importe_period
        else None
    )
    energy_bill = (
        max(
            0.0, annual_bill - (usage.fixed_charge_period or 0) * usage.periods_per_year
        )
        if annual_bill
        else None
    )
    saving = (
        round(energy_bill * coverage * NET_METERING_SAVING) if energy_bill else None
    )
    total = price * 1.16
    payback = round(total / saving, 1) if saving else None
    sizing.lines.append(
        {
            "handle": "sistema-solar-interconectado",
            "product_id": None,
            "description": (
                f"Sistema solar interconectado de {kwp:g} kWp: {panels} paneles de {watts} W, "
                "inversor de interconexión a la red, estructura, cableado, protecciones e "
                "instalación. Incluye asesoría para el contrato de interconexión con CFE."
            ),
            "unit_label": "Sistema",
            "quantity": 1,
            "unit_price": price,
            "iva_rate": 0.16,
        }
    )
    sizing.facts = {
        "hsp": hsp,
        "kwp": kwp,
        "panels": panels,
        "panel_w": watts,
        "annual_kwh": round(annual_kwh),
        "annual_generation_kwh": round(annual_gen),
        "coverage_pct": round(coverage * 100),
        "annual_bill_mxn": round(annual_bill) if annual_bill else None,
        "annual_fixed_charges_mxn": (
            round(usage.fixed_charge_period * usage.periods_per_year)
            if usage.fixed_charge_period
            else None
        ),
        "annual_saving_mxn": saving,
        "payback_years": payback,
        "formula": f"{interconectado_base():,.0f} + {interconectado_per_kw():,.0f} × kWp + IVA",
    }
    if kwp > max_instant_kwp():
        sizing.review.append("solar_large")
    if usage.tarifa and any(usage.tarifa.startswith(t) for t in MEDIUM_VOLTAGE_TARIFFS):
        sizing.review.append("solar_media_tension")
    if usage.state and usage.state != HOME_STATE:
        sizing.review.append("solar_install_outside")
    return sizing


def _spec(candidate: dict, key: str) -> float | None:
    return _positive((candidate.get("specs") or {}).get(key))


def size_aislado(usage: Usage | None, candidates: list[dict]) -> Sizing:
    sizing = Sizing("aislado")
    kits = sorted(
        (
            c
            for c in candidates
            if (c.get("specs") or {}).get("kind") == "aislado" and _spec(c, "daily_wh")
        ),
        key=lambda c: _spec(c, "daily_wh"),
    )
    if not kits:
        raise SolarUnavailable("no_candidates")
    need_wh = usage.daily_kwh * 1000 if usage else None
    if need_wh:
        fitting = [k for k in kits if _spec(k, "daily_wh") >= need_wh]
        pick = fitting[0] if fitting else kits[-1]
    else:
        pick = kits[0]
    sizing.choices = kits
    sizing.default_handle = pick["handle"]
    sizing.facts = {
        "need_wh_day": round(need_wh) if need_wh else None,
        "largest_kit_wh_day": round(_spec(kits[-1], "daily_wh")),
    }
    return sizing


def size_bombeo(answers: dict, usage: Usage | None, candidates: list[dict]) -> Sizing:
    sizing = Sizing("bombeo")
    source = answers.get("source") or "pozo"
    kits = [c for c in candidates if (c.get("specs") or {}).get("kind") == source]
    if not kits:
        raise SolarUnavailable("no_candidates")
    depth = _positive(answers.get("depth_m")) or 0.0
    lift = _positive(answers.get("lift_m")) or 0.0
    head = round((depth + lift) * HEAD_MARGIN, 1)
    daily_l = _positive(answers.get("daily_liters"))
    sizing.facts = {
        "source": source,
        "head_needed_m": head,
        "daily_liters_needed": round(daily_l) if daily_l else None,
        "sun_hours": PUMP_SUN_HOURS,
    }
    rated = [k for k in kits if _spec(k, "head_m") and _spec(k, "flow_lpm")]
    if not rated:
        # Surface kits have no published head/flow yet: let Claude pick the
        # likeliest one from the titles; an engineer confirms before payment.
        sizing.review.append("solar_specs")
        sizing.choices = sorted(kits, key=lambda k: float(k["price"]))
        sizing.default_handle = sizing.choices[0]["handle"]
        return sizing

    def daily_output(kit: dict) -> float:
        return _spec(kit, "flow_lpm") * 60 * PUMP_SUN_HOURS

    by_head = [k for k in rated if head <= _spec(k, "head_m") * KIT_HEAD_USE]
    if not by_head:
        sizing.review.append("solar_no_fit")
        strongest = max(rated, key=lambda k: _spec(k, "head_m"))
        sizing.choices = [strongest]
        sizing.default_handle = strongest["handle"]
        return sizing
    by_head.sort(key=lambda k: float(k["price"]))
    if daily_l:
        enough = [k for k in by_head if daily_output(k) >= daily_l]
        if enough:
            sizing.choices = enough
            sizing.default_handle = enough[0]["handle"]
        else:
            sizing.review.append("solar_no_fit")
            biggest = max(by_head, key=daily_output)
            sizing.choices = [biggest]
            sizing.default_handle = biggest["handle"]
    else:
        sizing.choices = by_head
        sizing.default_handle = by_head[0]["handle"]
    for kit in sizing.choices:
        kit.setdefault("daily_liters", round(daily_output(kit)))
    return sizing


def size(
    system: str, answers: dict, usage: Usage | None, candidates: list[dict]
) -> Sizing:
    if system == "interconectado":
        return size_interconectado(usage)
    if system == "aislado":
        return size_aislado(usage, candidates)
    return size_bombeo(answers, usage, candidates)


def kit_line(kit: dict) -> dict:
    return {
        "handle": kit["handle"],
        "product_id": kit.get("product_id"),
        "description": kit["title"],
        "unit_label": kit.get("unit_label") or "Kit",
        "quantity": 1,
        "unit_price": float(kit["price"]),
        "iva_rate": float(kit.get("iva_rate") or 0),
    }


# ── advice ───────────────────────────────────────────────────────────────────


def _advice_schema(handles: list[str]) -> dict:
    properties: dict = {
        "titulo": {"type": "string"},
        "resumen": {"type": "string"},
        "puntos": {"type": "array", "items": {"type": "string"}},
        "ahorro": _nullable({"type": "string"}),
        "siguiente_paso": {"type": "string"},
    }
    required = ["titulo", "resumen", "puntos", "ahorro", "siguiente_paso"]
    if handles:
        properties = {"handle": {"type": "string", "enum": handles}, **properties}
        required = ["handle", *required]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


ADVICE_PROMPT = """Eres el ingeniero de ventas de IMPAG / Todo Para El Campo (Nuevo Ideal, Durango). Un cliente pidió en la tienda en línea una cotización de "{system_name}" y aquí están los datos ya calculados (JSON):

{context}

{choose}Escribe la explicación que el cliente verá junto al precio, en español de México, de tú, clara y breve:
- titulo: una línea con el sistema recomendado (máx. 70 caracteres).
- resumen: 2 o 3 oraciones: qué leímos del recibo y por qué este sistema.
- puntos: de 3 a 5 viñetas cortas con datos concretos (consumo, tamaño, cobertura, agua al día, etc.).
- ahorro: una oración sobre el ahorro estimado y el retorno de la inversión si hay datos; si no hay datos o el ahorro no justifica la inversión, dilo con honestidad; null si no aplica.
- siguiente_paso: una oración que invite a pagar la cotización en línea o a pedir más información por WhatsApp. Si los datos traen "requiere_revision", en cambio di que un ingeniero confirma ese punto y le activa el pago en este mismo enlace (normalmente el mismo día), y que puede escribirnos por WhatsApp.

Reglas: usa sólo números que aparezcan en los datos (redondéalos); no menciones el precio, se muestra aparte; no inventes especificaciones ni plazos de CFE; si el sistema cubre sólo parte del consumo o la necesidad, dilo; marca las cifras de ahorro como estimadas."""


def _context(
    system: str,
    usage: Usage | None,
    bill: dict | None,
    answers: dict,
    sizing: Sizing,
) -> dict:
    context: dict = {"sistema": SYSTEM_NAMES[system]}
    if usage:
        context["recibo"] = {
            "tarifa": usage.tarifa,
            "periodo": usage.periodo,
            "periodos_leidos": usage.periods_read,
            "kwh_promedio_por_periodo": usage.avg_kwh_period,
            "kwh_por_mes": usage.monthly_kwh,
            "kwh_por_dia": usage.daily_kwh,
            "importe_promedio_por_periodo": usage.avg_importe_period,
            "estado": usage.state,
            "observaciones": (bill or {}).get("observaciones"),
        }
    else:
        context["recibo"] = None
    shown = {k: v for k, v in answers.items() if v not in (None, "")}
    if shown:
        context["respuestas_del_cliente"] = shown
    context["calculo"] = sizing.facts
    if sizing.choices:
        context["opciones"] = [
            {
                "handle": k["handle"],
                "nombre": k["title"],
                "precio_con_iva": round(
                    float(k["price"]) * (1 + float(k.get("iva_rate") or 0))
                ),
                **(k.get("specs") or {}),
                **(
                    {"litros_al_dia": k["daily_liters"]}
                    if k.get("daily_liters")
                    else {}
                ),
            }
            for k in sizing.choices
        ]
        context["opcion_sugerida"] = sizing.default_handle
    if sizing.review:
        context["requiere_revision"] = [REVIEW_LABELS[r] for r in sizing.review]
    return context


def advise(
    system: str, usage: Usage | None, bill: dict | None, answers: dict, sizing: Sizing
) -> dict:
    """Claude's pick (aislado/bombeo) and the buyer-facing explanation."""
    handles = [k["handle"] for k in sizing.choices]
    choose = (
        "Elige en `handle` la opción que mejor cubra la necesidad del cliente; la sugerida "
        "es la más económica que cumple. Cambia sólo si los datos lo justifican (p. ej. un "
        "consumo de bombeo en el recibo que pida un equipo mayor, o lo que el cliente dijo "
        "que quiere conectar).\n\n"
        if len(handles) > 1
        else ""
    )
    prompt = ADVICE_PROMPT.format(
        system_name=SYSTEM_NAMES[system],
        context=json.dumps(
            _context(system, usage, bill, answers, sizing), ensure_ascii=False, indent=1
        ),
        choose=choose,
    )
    data = _structured_call(
        [{"type": "text", "text": prompt}],
        _advice_schema(handles),
        effort="low",
        timeout=ADVICE_TIMEOUT_S,
        max_tokens=4000,
    )
    if handles and data.get("handle") not in handles:
        data["handle"] = sizing.default_handle
    return data


def template_advice(
    system: str, usage: Usage | None, sizing: Sizing, kit: dict | None
) -> dict:
    """The explanation when the advice call fails: facts only, no flourish."""
    points: list[str] = []
    if usage:
        points.append(
            f"Consumo promedio: {usage.monthly_kwh:,.0f} kWh al mes (tarifa {usage.tarifa or 'sin dato'})."
        )
    facts = sizing.facts
    if system == "interconectado":
        title = f"Sistema interconectado de {facts['kwp']:g} kWp"
        points.append(
            f"{facts['panels']} paneles de {facts['panel_w']} W; cubre cerca del {facts['coverage_pct']} % de tu consumo."
        )
        saving = (
            f"Ahorro estimado: ${facts['annual_saving_mxn']:,.0f} al año."
            if facts.get("annual_saving_mxn")
            else None
        )
    else:
        title = kit["title"] if kit else SYSTEM_NAMES[system]
        saving = None
        if system == "bombeo" and facts.get("head_needed_m"):
            points.append(
                f"Altura de bombeo considerada: {facts['head_needed_m']:g} m."
            )
    return {
        "titulo": title[:90],
        "resumen": f"Armamos tu {SYSTEM_NAMES[system].lower()} con los datos de tu recibo y tus respuestas.",
        "puntos": points,
        "ahorro": saving,
        "siguiente_paso": (
            "Un ingeniero confirma los detalles y activa el pago en este mismo enlace; "
            "también puedes escribirnos por WhatsApp."
            if sizing.review
            else "Puedes pagar tu cotización en línea o pedirnos más información por WhatsApp."
        ),
    }


# ── the whole request ────────────────────────────────────────────────────────


def _bill_summary(usage: Usage | None, bill: dict | None) -> dict | None:
    if usage is None:
        return None
    rows = [
        {"periodo": r.get("periodo"), "kwh": r.get("kwh"), "importe": r.get("importe")}
        for r in (bill or {}).get("historial") or []
        if isinstance(r, dict) and _positive(r.get("kwh"))
    ][:12]
    return {
        "tarifa": usage.tarifa,
        "periodo": usage.periodo,
        "kwh_por_mes": usage.monthly_kwh,
        "kwh_promedio_por_periodo": usage.avg_kwh_period,
        "importe_promedio_por_periodo": usage.avg_importe_period,
        "estado": (bill or {}).get("estado"),
        "municipio": (bill or {}).get("municipio"),
        "historial": rows,
    }


def _public_notes(advice: dict, usage_text: str | None) -> str:
    """Shown to the buyer on /cotizacion/<token> (outside the JSON block)."""
    parts = [f"{advice.get('titulo', '').strip()}", advice.get("resumen", "").strip()]
    parts += [f"• {p.strip()}" for p in advice.get("puntos") or [] if p and p.strip()]
    if advice.get("ahorro"):
        parts.append(advice["ahorro"].strip())
    parts.append(
        "Cotización generada con inteligencia artificial a partir de tu recibo de CFE; "
        "las cifras de consumo y ahorro son estimadas."
    )
    text = "\n".join(p for p in parts if p)
    if usage_text:
        text += f"\n\nComentarios del cliente:\n{usage_text}"
    return text[:4000]


def create_solar_quote(
    db: Session,
    *,
    system: str,
    files: list[tuple[bytes, str]],
    answers: dict,
    customer: Any,
    candidates: list[dict],
) -> dict:
    """Read the bill, size, advise and record the quote. Raises BillUnreadable,
    SolarUnavailable, SolarLimitReached, or web_quotes' QuoteRequestRejected /
    QuoteLimitReached."""
    bill = read_bill(files) if files else None
    usage = usage_from_bill(bill, getattr(customer, "location", None))
    if files and usage is None:
        raise BillUnreadable()
    sizing = size(system, answers, usage, candidates)

    advice: dict
    try:
        advice = advise(system, usage, bill, answers, sizing)
    except SolarUnavailable as exc:
        logger.warning("solar quote: advice fell back to the template (%s)", exc.reason)
        advice = None  # type: ignore[assignment]
    by_handle = {k["handle"]: k for k in sizing.choices}
    kit = by_handle.get((advice or {}).get("handle") or sizing.default_handle or "")
    if advice is None:
        advice = template_advice(system, usage, sizing, kit)
    if kit is not None:
        sizing.lines = [kit_line(kit)]
        sizing.facts["kit"] = kit["handle"]
        for key in ("daily_wh", "head_m", "flow_lpm"):
            if _spec(kit, key):
                sizing.facts[f"kit_{key}"] = _spec(kit, key)
        if kit.get("daily_liters"):
            sizing.facts["kit_daily_liters"] = kit["daily_liters"]
        need = sizing.facts.get("need_wh_day")
        if system == "aislado" and need and _spec(kit, "daily_wh"):
            sizing.facts["coverage_pct"] = min(
                100, round(_spec(kit, "daily_wh") / need * 100)
            )
    if not sizing.lines:
        raise SolarUnavailable("no_candidates")

    usage_text = (answers.get("usage") or "").strip() or None
    solar_block = {
        "system": system,
        "bill": (
            None
            if bill is None
            else {
                k: bill.get(k)
                for k in (
                    "tarifa",
                    "periodo",
                    "periodo_inicio",
                    "periodo_fin",
                    "consumo_kwh",
                    "importe_periodo",
                    "total_a_pagar",
                    "cargo_fijo",
                    "historial",
                    "demanda_kw",
                    "numero_servicio",
                    "titular",
                    "municipio",
                    "estado",
                    "calidad",
                    "observaciones",
                )
            }
        ),
        "usage": None if usage is None else usage.__dict__,
        "answers": {k: v for k, v in answers.items() if v not in (None, "")},
        "sizing": sizing.facts,
        "model": model(),
    }
    req = SimpleNamespace(
        customer=customer,
        delivery=SimpleNamespace(method="recoger", address=None),
        invoice=None,
        items=[
            SimpleNamespace(sku=None, **line)  # handle, product_id, description, …
            for line in sizing.lines
        ],
        notes=None,
    )
    result = create_quote_request(
        db,
        req,
        extra_reasons=sizing.review,
        block_extra={"solar": solar_block},
        notes_text=_public_notes(advice, usage_text),
    )
    result.update(
        {
            "system": system,
            "bill": _bill_summary(usage, bill),
            "sizing": sizing.facts,
            "advice": {
                k: advice.get(k)
                for k in ("titulo", "resumen", "puntos", "ahorro", "siguiente_paso")
            },
            "items": [
                {
                    "handle": line["handle"],
                    "description": line["description"],
                    "quantity": line["quantity"],
                    "unit_price": line["unit_price"],
                    "iva_rate": line["iva_rate"],
                }
                for line in sizing.lines
            ],
        }
    )
    return result
