from __future__ import annotations

import json
import os
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict

import boto3
import stripe


STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DEFAULT_CURRENCY = os.getenv("STRIPE_DEFAULT_CURRENCY", "usd")
WEBHOOK_TARGET_LAMBDA_ARN = os.getenv("WEBHOOK_TARGET_LAMBDA_ARN", "")
# Note: If this Lambda runs inside a VPC, it needs outbound access to the Lambda
# API to invoke another function. Use a NAT Gateway or a VPC Interface Endpoint
# for Lambda (com.amazonaws.<region>.lambda). The target Lambda can be in or out
# of a VPC; invocation is handled by AWS. Ensure IAM allows lambda:InvokeFunction.

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


def _http_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_event_body(event: Dict[str, Any]) -> Dict[str, Any]:
    if "body" not in event:
        return event

    body = event["body"]
    if not body:
        return {}

    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def create_checkout_session(event: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("checkout: start")
    if not STRIPE_API_KEY:
        logger.error("checkout: STRIPE_API_KEY not set")
        return _http_response(500, {"error": "STRIPE_API_KEY is not set"})

    payload = _parse_event_body(event)
    amount = payload.get("amount")
    if amount is None:
        amount = payload.get("line_items[0][price_data][unit_amount]")
    currency = payload.get("currency")
    if currency is None:
        currency = payload.get("line_items[0][price_data][currency]")
    product_name = payload.get("product_name")
    if product_name is None:
        product_name = payload.get("line_items[0][price_data][product_data][name]")
    quantity = payload.get("quantity")
    if quantity is None:
        quantity = payload.get("line_items[0][quantity]")
    if amount is None:
        logger.warning("checkout: missing amount")
        return _http_response(400, {"error": "Missing required parameter: amount"})

    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        logger.warning("checkout: invalid amount")
        return _http_response(400, {"error": "amount must be an integer (in smallest currency unit)"})

    if amount_int <= 0:
        logger.warning("checkout: amount <= 0")
        return _http_response(400, {"error": "amount must be greater than 0"})

    currency = currency or DEFAULT_CURRENCY
    success_url = payload.get("success_url")
    cancel_url = payload.get("cancel_url")
    if not success_url or not cancel_url:
        logger.warning("checkout: missing success/cancel url")
        return _http_response(400, {"error": "Missing required parameters: success_url, cancel_url"})

    metadata = payload.get("metadata") or {}
    mode = payload.get("mode") or "payment"
    line_items = payload.get("line_items")
    payment_method_types = payload.get("payment_method_types")
    if not payment_method_types:
        payment_method_types = [
            payload.get("payment_method_types[0]"),
            payload.get("payment_method_types[1]"),
            payload.get("payment_method_types[2]"),
        ]
        payment_method_types = [p for p in payment_method_types if p]

    try:
        form: Dict[str, Any] = {
            "success_url": success_url,
            "cancel_url": cancel_url,
            "mode": mode,
        }

        for key, value in payload.items():
            if key.startswith("line_items[") or key.startswith("payment_method_types[") or key.startswith("metadata[") or key.startswith("payment_intent_data["):
                form[key] = value

        if "line_items[0][price_data][currency]" not in form:
            form["line_items[0][price_data][currency]"] = currency
        if "line_items[0][price_data][product_data][name]" not in form:
            form["line_items[0][price_data][product_data][name]"] = product_name or "Stripe Checkout"
        if "line_items[0][price_data][unit_amount]" not in form:
            form["line_items[0][price_data][unit_amount]"] = str(amount_int)
        if "line_items[0][quantity]" not in form:
            form["line_items[0][quantity]"] = str(int(quantity) if quantity is not None else 1)

        customer_email = payload.get("customer_email")
        if customer_email:
            form["customer_email"] = customer_email

        if metadata:
            for meta_key, meta_value in metadata.items():
                form.setdefault(f"metadata[{meta_key}]", meta_value)

        if payment_method_types:
            for idx, method in enumerate(payment_method_types):
                form.setdefault(f"payment_method_types[{idx}]", method)

        url = "https://api.stripe.com/v1/checkout/sessions"
        data = urllib.parse.urlencode(form).encode("utf-8")
        print('data : ')
        print(data)
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {STRIPE_API_KEY}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=120) as resp:            
            response_body = resp.read().decode("utf-8")
            session = json.loads(response_body)
            print(json.dumps(session))
    except Exception as exc:
        print("checkout: stripe error")
        logger.exception("checkout: stripe error")
        return _http_response(500, {"error": "Stripe error", "detail": str(exc)})

    logger.info("checkout: created session %s", session.get("id"))
    return _http_response(200, {"session": session})


def handle_webhook(event: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("webhook: start")
    if not STRIPE_WEBHOOK_SECRET:
        logger.error("webhook: STRIPE_WEBHOOK_SECRET not set")
        return _http_response(500, {"error": "STRIPE_WEBHOOK_SECRET is not set"})

    payload = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        payload = base64.b64decode(payload).decode("utf-8")

    signature = (event.get("headers") or {}).get("stripe-signature")
    if not signature:
        logger.warning("webhook: missing stripe-signature header")
        return _http_response(400, {"error": "Missing Stripe signature"})

    try:
        stripe_event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        logger.warning("webhook: invalid signature")
        return _http_response(400, {"error": "Invalid webhook signature", "detail": str(exc)})

    event_type = stripe_event.get("type")
    logger.info("webhook: verified event %s (%s)", stripe_event.get("id"), event_type)

    if WEBHOOK_TARGET_LAMBDA_ARN:
        try:
            lambda_client = boto3.client("lambda")
            lambda_client.invoke(
                FunctionName=WEBHOOK_TARGET_LAMBDA_ARN,
                InvocationType="Event",
                Payload=json.dumps({"stripe_event": stripe_event}).encode("utf-8"),
            )
            logger.info("webhook: forwarded event to target lambda")
        except Exception:
            logger.exception("webhook: failed to invoke target lambda")
    else:
        logger.warning("webhook: WEBHOOK_TARGET_LAMBDA_ARN not set; skipping invoke")
    event_data = stripe_event.get("data", {}).get("object", {})

    return _http_response(
        200,
        {
            "received": True,
            "event_type": event_type,
            "event_id": stripe_event.get("id"),
            "object": event_data,
        },
    )
