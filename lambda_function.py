from __future__ import annotations

import json
from typing import Any, Dict

from main import create_checkout_session, handle_webhook


def _event_with_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"body": json.dumps(payload)}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if isinstance(event, dict) and "action" in event:
        action = event.get("action")
        payload = event.get("payload") or {}

        if action == "create_checkout_session":
            return create_checkout_session(_event_with_body(payload))
        if action == "handle_webhook":
            return handle_webhook(_event_with_body(payload))

        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": f"Unknown action: {action}"}),
        }

    route_key = None
    if isinstance(event, dict):
        route_key = event.get("route") or event.get("routeKey") or event.get("resource") or event.get("path")

    if route_key and "webhook" in str(route_key).lower():
        return handle_webhook(event)

    return create_checkout_session(event)
