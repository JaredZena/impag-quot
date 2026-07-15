"""
WhatsApp Cloud API sender.

HARD SEND GATE: while config.wa_sending_enabled is False, send_text_message()
does a dry run — it returns without ever calling Meta. This is the boundary the
whole human-in-the-loop design hangs on: approvals are recorded and the pipeline
runs end to end, but nothing reaches a real customer until sending is explicitly
turned on (WA_SENDING_ENABLED=true) after sign-off + a permanent token + a real
number. Keep it that way.
"""
from typing import Dict

import requests

import config


def send_text_message(to_phone: str, text: str) -> Dict:
    """
    Send a WhatsApp text message. Returns a result dict; never raises for the
    normal HTTP/gate failure modes so callers can record the outcome.

    Dry run (gate off): {"sent": False, "dry_run": True, ...}
    """
    if not config.wa_sending_enabled:
        return {"sent": False, "dry_run": True,
                "reason": "WA_SENDING_ENABLED is false — message NOT sent (gate on)."}

    if not (config.wa_phone_number_id and config.wa_access_token):
        return {"sent": False, "dry_run": False, "error": "WhatsApp credentials not configured"}

    url = f"https://graph.facebook.com/{config.wa_graph_version}/{config.wa_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {config.wa_access_token}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code == 200:
            wamid = (data.get("messages") or [{}])[0].get("id")
            return {"sent": True, "dry_run": False, "wa_message_id": wamid, "status_code": 200}
        return {"sent": False, "dry_run": False, "status_code": resp.status_code,
                "error": (data.get("error") or {}).get("message", "send failed")}
    except Exception as e:
        return {"sent": False, "dry_run": False, "error": str(e)}
