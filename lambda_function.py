from __future__ import annotations

from typing import Any, Dict

from main import create_checkout_session, handle_webhook


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    route_key = None
    if isinstance(event, dict):
        route_key = event.get("route") or event.get("routeKey") or event.get("resource") or event.get("path")

    if route_key and "webhook" in str(route_key).lower():
        return handle_webhook(event)

    return create_checkout_session(event)
