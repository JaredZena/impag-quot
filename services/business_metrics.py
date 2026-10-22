"""Outside numbers for the "Cómo vamos" panel on /hoy (routes/panel.py).

Where each number comes from, until Google approves the Business Profile API:
- Business Profile, per month: Google's own "informe de rendimiento" email to
  impagtodoparaelcampo@gmail.com (~6th of the next month; history since
  2024-01). Views, calls, directions, website and chat clicks.
- Reviews: one "X dejó una opinión sobre IMPAG" email per review (author,
  stars). The count is the reviews Google notified, not Maps' live total.
- Search Console: clicks and impressions per day for todoparaelcampo.com.mx
  (searchanalytics API, ~2–3 days behind).

All of them read with one OAuth refresh token of impagtodoparaelcampo@gmail.com
(scopes gmail.readonly + webmasters.readonly + analytics.readonly): GOOGLE_METRICS_REFRESH_TOKEN,
minted with GOOGLE_METRICS_CLIENT_ID/SECRET (falls back to
GOOGLE_OAUTH_CLIENT_ID/SECRET). Each part fails on its own, so a missing
Search Console scope never blocks the Business Profile numbers.

The store's traffic comes from Google Analytics 4 (property GA4_PROPERTY_ID,
stream "Tienda" on todoparaelcampo.com.mx, live since 2026-10-22): sessions,
users and page views per day plus every event per day (whatsapp_click,
quote_add, buy_click, ...), read with the same token (analytics.readonly).
Umami Cloud's free plan has no API, so it is not read here.

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
GA4_DATA = "https://analyticsdata.googleapis.com/v1beta"
GA4_PROPERTY_ID = "560374705"  # account IMPAG 413131302, stream "Tienda"
GA4_DAY_METRICS = {
    "sessions": "visits",
    "activeUsers": "users",
    "screenPageViews": "pageviews",
}

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


# ── Google Analytics 4 (store traffic) ──────────────────────────────────────


def _ga4_day(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))  # "20261022"


def parse_ga4_days(report: dict) -> dict[date, dict[str, float]]:
    """runReport by date with GA4_DAY_METRICS → {day: {"visits": n, ...}}."""
    names = [
        GA4_DAY_METRICS.get(h["name"], h["name"])
        for h in report.get("metricHeaders", [])
    ]
    out: dict[date, dict[str, float]] = {}
    for row in report.get("rows", []):
        day = _ga4_day(row["dimensionValues"][0]["value"])
        out[day] = {
            name: float(v["value"]) for name, v in zip(names, row["metricValues"])
        }
    return out


def parse_ga4_events(report: dict) -> dict[date, dict[str, float]]:
    """runReport by date + eventName with eventCount → {day: {event: n}}."""
    out: dict[date, dict[str, float]] = {}
    for row in report.get("rows", []):
        day_value, event = (d["value"] for d in row["dimensionValues"])
        out.setdefault(_ga4_day(day_value), {})[event[:40]] = float(
            row["metricValues"][0]["value"]
        )
    return out


def _ga4_report(token: str, property_id: str, body: dict) -> dict:
    resp = requests.post(
        f"{GA4_DATA}/properties/{property_id}:runReport",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def sync_ga4(db: Session, token: str, days: int = 120) -> dict:
    property_id = os.getenv("GA4_PROPERTY_ID", GA4_PROPERTY_ID)
    window = [{"startDate": f"{days}daysAgo", "endDate": "today"}]
    daily = parse_ga4_days(
        _ga4_report(
            token,
            property_id,
            {
                "dateRanges": window,
                "dimensions": [{"name": "date"}],
                "metrics": [{"name": m} for m in GA4_DAY_METRICS],
                "limit": days + 5,
            },
        )
    )
    for day, events in parse_ga4_events(
        _ga4_report(
            token,
            property_id,
            {
                "dateRanges": window,
                "dimensions": [{"name": "date"}, {"name": "eventName"}],
                "metrics": [{"name": "eventCount"}],
                "limit": 10000,
            },
        )
    ).items():
        daily.setdefault(day, {}).update(events)

    counts = {"new": 0, "updated": 0, "same": 0}
    for day, metrics in daily.items():
        for metric, value in metrics.items():
            counts[_upsert(db, "ga4", metric, "day", day, value)] += 1
    return {
        "property": property_id,
        "days": len(daily),
        "latest_day": max(daily).isoformat() if daily else None,
        **counts,
    }


def sync_google_metrics(db: Session, dry_run: bool = False) -> dict:
    """Business Profile, Search Console and GA4, each on its own: one part
    failing never blocks the others."""
    result = {"dry_run": dry_run}
    token = get_metrics_access_token()
    for name, fn in (
        ("business_profile", sync_business_profile),
        ("search_console", sync_search_console),
        ("analytics", sync_ga4),
    ):
        try:
            result[name] = fn(db, token)
        except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
            db.rollback()
            result[name] = {"error": str(e)[:300]}
            continue
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return result
