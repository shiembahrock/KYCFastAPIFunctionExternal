from __future__ import annotations
import json
from typing import Any, Dict
from main import create_checkout_session, stripe_webhook, send_email, send_email_smtp, get_muinmos_token, create_assessment, muinmos_assessment_search, send_muinmos_assessment_kycpdf

def _event_with_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"body": json.dumps(payload)}

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if isinstance(event, dict) and "action" in event:
        action = event.get("action")
        payload = event.get("payload") or {}

        if action == "send_muinmos_assessment_kycpdf":
            return send_muinmos_assessment_kycpdf(
                base_api_url=payload["base_api_url"],
                token_type=payload["token_type"],
                access_token=payload["access_token"],
                assessment_list=payload["assessment_list"]
            )
        if action == "muinmos_assessment_search":
            return muinmos_assessment_search(
                from_date=payload["from_date"],
                to_date=payload["to_date"],
                base_api_url=payload["base_api_url"],
                token_type=payload["token_type"],
                access_token=payload["access_token"]
            )
        if action == "create_assessment":
            return create_assessment(
                user_email=payload["user_email"],
                order_code=payload["order_code"],
                api_url=payload["api_url"],
                token_type=payload["token_type"],
                access_token=payload["access_token"]
            )
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
                attachment=payload.get("attachment")
            )
        if action == "send_email_smtp":
            return send_email_smtp(
                to_email=payload["to_email"],
                subject=payload["subject"],
                body=payload["body"],
                is_html=payload.get("is_html", False),
                attachment=payload.get("attachment")
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
