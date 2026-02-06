from __future__ import annotations
import json
from typing import Any, Dict
from main import create_checkout_session, stripe_webhook, send_email, get_muinmos_token

def _event_with_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"body": json.dumps(payload)}

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if isinstance(event, dict) and "action" in event:
        action = event.get("action")
        payload = event.get("payload") or {}

        if action == "get_muinmos_token":
            return get_muinmos_token(
                grant_type=payload["grant_type"],
                client_id=payload["client_id"],
                client_secret=payload["client_secret"],
                username=payload["username"],
                password=payload["password"],
                api_url=payload["api_url"]
            )
        if action == "send_email":
            return send_email(
                to_email=payload["to_email"],
                subject=payload["subject"],
                body=payload["body"],
                is_html=payload.get("is_html", False),
            )
        if action == "create_checkout_session":
            return create_checkout_session(_event_with_body(payload))
        if action == "stripe_webhook":
            return stripe_webhook(_event_with_body(payload))

        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": f"Unknown action: {action}"}),
        }

    route_key = None
    if isinstance(event, dict):
        route_key = event.get("route") or event.get("routeKey") or event.get("resource") or event.get("path")

    if route_key and "stripewebhook" in str(route_key).lower():
        return stripe_webhook(event)

    return create_checkout_session(event)
