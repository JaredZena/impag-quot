"""Outside numbers for the "Cómo vamos" panel on /hoy (routes/panel.py).

Where each number comes from, until Google approves the Business Profile API:
- Business Profile, per month: Google's own "informe de rendimiento" email to
  impagtodoparaelcampo@gmail.com (~6th of the next month; history since
  2024-01). Views, calls, directions, website and chat clicks.
- Reviews: one "X dejó una opinión sobre IMPAG" email per review (author,
  stars). The count is the reviews Google notified, not Maps' live total.
- Search Console: clicks and impressions per day for todoparaelcampo.com.mx
  (searchanalytics API, ~2–3 days behind).

All three read with one OAuth refresh token of impagtodoparaelcampo@gmail.com
(scopes gmail.readonly + webmasters.readonly): GOOGLE_METRICS_REFRESH_TOKEN,
minted with GOOGLE_METRICS_CLIENT_ID/SECRET (falls back to
GOOGLE_OAUTH_CLIENT_ID/SECRET). Each part fails on its own, so a missing
Search Console scope never blocks the Business Profile numbers.

The store's traffic comes from Umami Cloud (todoparaelcampo.com.mx): visits
and pageviews per day plus every tracked event per day (whatsapp-click,
quote-add, buy-click, ...), read with UMAMI_API_KEY (Umami → Settings → API
keys). Unset = that part is skipped, not an error.

Rows land in business_metric (models.BusinessMetric), upserted, so the daily
job (POST /jobs/google-metrics-sync) can re-read everything safely.
"""

import base64
import html
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from models import BusinessMetric
from services.quote_capture import BUSINESS_TZ

TIMEOUT = 30
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GSC = "https://searchconsole.googleapis.com/webmasters/v3"
GBP_SENDER = "businessprofile-noreply@google.com"
SITE_HINT = "todoparaelcampo"
UMAMI_API = "https://api.umami.is/v1"
UMAMI_WEBSITE_ID = "98e9eb72-3cf3-43e6-96cb-6b0a1b8d65ac"  # storefront index.html
UMAMI_TZ = "America/Mexico_City"

MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# First match after "Un resumen de tu rendimiento". Before 2025 Google said
# "N mensajes" where it now says "N clics en el chat".
REPORT_PATTERNS = {
    "calls": r"(\d[\d,.]*)\s+llamadas?\b",
    "chat_clicks": r"(\d[\d,.]*)\s+(?:clics? en el chat|mensajes?)\b",
    "directions": r"(\d[\d,.]*)\s+personas?\s+solicit\w+\s+instrucciones",
    "website_clicks": r"(\d[\d,.]*)\s+visitas?\s+al\s+sitio\s+web",
    "profile_views": r"(\d[\d,.]*)\s+vistas?\s+del\s+perfil",
}


class MetricsNotConfigured(RuntimeError):
    pass


def get_metrics_access_token() -> str:
    client_id = os.getenv("GOOGLE_METRICS_CLIENT_ID") or os.getenv(
        "GOOGLE_OAUTH_CLIENT_ID"
    )
    client_secret = os.getenv("GOOGLE_METRICS_CLIENT_SECRET") or os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRET"
    )
    refresh_token = os.getenv("GOOGLE_METRICS_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise MetricsNotConfigured(
            "Falta GOOGLE_METRICS_REFRESH_TOKEN (cuenta impagtodoparaelcampo)"
        )
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Google OAuth token refresh failed (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        )
    return resp.json()["access_token"]


# ── parsing (pure, tested) ───────────────────────────────────────────────────


def html_to_text(raw_html: str) -> str:
    raw_html = re.sub(
        r"<style.*?</style>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE
    )
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw_html))
    # Gmail preheaders pad with zero-width joiners / combining marks.
    return re.sub("[\\s\u200b\u200c\u200d\u034f\ufeff]+", " ", text).strip()


def _int(value: str) -> int:
    return int(re.sub(r"[,.]", "", value))


def report_month(subject: str) -> date | None:
    """'IMPAG, aquí está tu informe de rendimiento para septiembre de 2026'."""
    m = re.search(r"para\s+([a-záéíóú]+)\s+de\s+(\d{4})", subject or "", re.IGNORECASE)
    if not m or m.group(1).lower() not in MONTHS_ES:
        return None
    return date(int(m.group(2)), MONTHS_ES[m.group(1).lower()], 1)


def parse_report(text: str) -> dict[str, int]:
    i = text.find("Un resumen de tu rendimiento")
    if i < 0:
        return {}
    summary = text[i : i + 600]
    out = {}
    for metric, pattern in REPORT_PATTERNS.items():
        m = re.search(pattern, summary, re.IGNORECASE)
        if m:
            out[metric] = _int(m.group(1))
    if out:
        out["interactions"] = sum(
            out.get(k, 0)
            for k in ("calls", "chat_clicks", "directions", "website_clicks")
        )
    return out


def parse_review(subject: str, text: str) -> tuple[str, int] | None:
    """'Luis dejó una opinión sobre IMPAG' + '... nueva opinión de 5 estrellas'."""
    m = re.match(r"\s*(.+?)\s+dejó una opinión", subject or "")
    if not m:
        return None
    stars = re.search(r"opinión de (\d) estrellas?", text)
    return m.group(1).strip()[:200], int(stars.group(1)) if stars else 0


# ── Gmail ────────────────────────────────────────────────────────────────────


def _get(url: str, token: str, **params) -> dict:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _gmail_ids(token: str, query: str) -> list[str]:
    ids, page = [], None
    while True:
        params = {"q": query, "maxResults": 100}
        if page:
            params["pageToken"] = page
        data = _get(f"{GMAIL}/messages", token, **params)
        ids += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            return ids


def _gmail_message(token: str, message_id: str) -> tuple[str, str, date]:
    msg = _get(f"{GMAIL}/messages/{message_id}", token, format="full")
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

    def parts(p):
        yield p
        for child in p.get("parts", []) or []:
            yield from parts(child)

    body = ""
    for p in parts(msg["payload"]):
        data = (p.get("body") or {}).get("data")
        if data and p.get("mimeType") == "text/html":
            body = base64.urlsafe_b64decode(data).decode("utf-8", "ignore")
            break
    received = datetime.fromtimestamp(
        int(msg.get("internalDate", 0)) / 1000, tz=BUSINESS_TZ
    ).date()
    return headers.get("subject", ""), html_to_text(body), received


# ── upsert ───────────────────────────────────────────────────────────────────


def _upsert(db: Session, source, metric, period, start, value, label=None, ref=""):
    row = (
        db.query(BusinessMetric)
        .filter_by(
            source=source, metric=metric, period=period, period_start=start, ref=ref
        )
        .one_or_none()
    )
    value = Decimal(str(value))
    if row is None:
        db.add(
            BusinessMetric(
                source=source,
                metric=metric,
                period=period,
                period_start=start,
                value=value,
                label=label,
                ref=ref,
            )
        )
        return "new"
    if row.value != value or row.label != label:
        row.value, row.label = value, label
        return "updated"
    return "same"


# ── syncs ────────────────────────────────────────────────────────────────────


def sync_business_profile(db: Session, token: str) -> dict:
    months, reviews = {}, []
    for mid in _gmail_ids(token, f'from:{GBP_SENDER} subject:"informe de rendimiento"'):
        subject, text, _ = _gmail_message(token, mid)
        month = report_month(subject)
        values = parse_report(text)
        if month and values:
            months[month] = values
    for mid in _gmail_ids(token, f'from:{GBP_SENDER} "dejó una opinión"'):
        subject, text, received = _gmail_message(token, mid)
        parsed = parse_review(subject, text)
        if parsed:
            reviews.append((mid, received, *parsed))

    counts = {"new": 0, "updated": 0, "same": 0}
    for month, values in months.items():
        for metric, value in values.items():
            counts[_upsert(db, "gbp", metric, "month", month, value)] += 1
    for mid, received, author, stars in reviews:
        counts[
            _upsert(db, "gbp_review", "stars", "event", received, stars, author, mid)
        ] += 1
    return {
        "months": len(months),
        "latest_month": max(months).isoformat() if months else None,
        "reviews": len(reviews),
        **counts,
    }


def _gsc_site(token: str) -> str:
    configured = os.getenv("GSC_SITE_URL")
    if configured:
        return configured
    sites = _get(f"{GSC}/sites", token).get("siteEntry", [])
    for s in sites:
        if (
            SITE_HINT in s.get("siteUrl", "")
            and s.get("permissionLevel") != "siteUnverifiedUser"
        ):
            return s["siteUrl"]
    raise RuntimeError(f"Search Console: no hay propiedad de {SITE_HINT} en la cuenta")


def sync_search_console(db: Session, token: str, days: int = 120) -> dict:
    site = _gsc_site(token)
    end = datetime.now(BUSINESS_TZ).date()
    start = end - timedelta(days=days)
    resp = requests.post(
        f"{GSC}/sites/{quote(site, safe='')}/searchAnalytics/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["date"],
            "rowLimit": days + 5,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    counts = {"new": 0, "updated": 0, "same": 0}
    for r in rows:
        day = date.fromisoformat(r["keys"][0])
        counts[_upsert(db, "gsc", "clicks", "day", day, r.get("clicks", 0))] += 1
        counts[
            _upsert(db, "gsc", "impressions", "day", day, r.get("impressions", 0))
        ] += 1
    return {
        "site": site,
        "days": len(rows),
        "latest_day": rows[-1]["keys"][0] if rows else None,
        **counts,
    }


# ── Umami (store traffic) ────────────────────────────────────────────────────


def _series_day(x) -> date:
    """Umami series points carry "2026-10-01 00:00:00" (already in the
    requested timezone) or an ISO timestamp; the day is the first 10 chars."""
    return date.fromisoformat(str(x)[:10])


def parse_umami_pageviews(data: dict) -> dict[date, dict[str, float]]:
    """/pageviews?unit=day → {day: {"pageviews": n, "visits": n}}."""
    out: dict[date, dict[str, float]] = {}
    for key, metric in (("pageviews", "pageviews"), ("sessions", "visits")):
        for point in (data or {}).get(key) or []:
            out.setdefault(_series_day(point["x"]), {})[metric] = point.get("y", 0)
    return out


def parse_umami_events(data) -> dict[date, dict[str, float]]:
    """/events/series?unit=day → {day: {event_name: n}}."""
    rows = data.get("data", data) if isinstance(data, dict) else data
    out: dict[date, dict[str, float]] = {}
    for point in rows or []:
        name, when = point.get("x"), point.get("t")
        if not name or not when:
            continue
        day = out.setdefault(_series_day(when), {})
        day[str(name)[:40]] = day.get(str(name)[:40], 0) + point.get("y", 0)
    return out


def _umami_get(path: str, key: str, **params):
    resp = requests.get(
        f"{os.getenv('UMAMI_API_BASE', UMAMI_API)}{path}",
        headers={"x-umami-api-key": key, "Accept": "application/json"},
        params=params,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def sync_umami(db: Session, days: int = 120) -> dict:
    key = os.getenv("UMAMI_API_KEY")
    if not key:
        return {"skipped": "Falta UMAMI_API_KEY"}
    site = os.getenv("UMAMI_WEBSITE_ID", UMAMI_WEBSITE_ID)
    now = datetime.now(BUSINESS_TZ)
    start = datetime.combine(
        now.date() - timedelta(days=days), datetime.min.time(), tzinfo=BUSINESS_TZ
    )
    window = {
        "startAt": int(start.timestamp() * 1000),
        "endAt": int(now.timestamp() * 1000),
        "unit": "day",
        "timezone": UMAMI_TZ,
    }
    daily = parse_umami_pageviews(
        _umami_get(f"/websites/{site}/pageviews", key, **window)
    )
    for day, events in parse_umami_events(
        _umami_get(f"/websites/{site}/events/series", key, **window)
    ).items():
        daily.setdefault(day, {}).update(events)

    counts = {"new": 0, "updated": 0, "same": 0}
    for day, metrics in daily.items():
        for metric, value in metrics.items():
            counts[_upsert(db, "umami", metric, "day", day, value)] += 1
    return {
        "days": len(daily),
        "latest_day": max(daily).isoformat() if daily else None,
        **counts,
    }


def sync_google_metrics(db: Session, dry_run: bool = False) -> dict:
    """Google (Business Profile + Search Console) and Umami, each on its own:
    one part failing or unconfigured never blocks the others."""
    result = {"dry_run": dry_run}
    try:
        token, google_error = get_metrics_access_token(), None
    except (RuntimeError, requests.RequestException) as e:
        token, google_error = None, str(e)[:300]
    for name, fn in (
        ("business_profile", lambda: sync_business_profile(db, token)),
        ("search_console", lambda: sync_search_console(db, token)),
        ("umami", lambda: sync_umami(db)),
    ):
        if name != "umami" and token is None:
            result[name] = {"error": google_error}
            continue
        try:
            result[name] = fn()
        except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
            db.rollback()
            result[name] = {"error": str(e)[:300]}
            continue
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return result
