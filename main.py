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
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DEFAULT_CURRENCY = os.getenv("STRIPE_DEFAULT_CURRENCY", "usd")
WEBHOOK_TARGET_LAMBDA_ARN = os.getenv("WEBHOOK_TARGET_LAMBDA_ARN", "")
SES_FROM_EMAIL = os.getenv("SES_FROM_EMAIL", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
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
        
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {STRIPE_API_KEY}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=120) as resp:            
            response_body = resp.read().decode("utf-8")
            session = json.loads(response_body)
    except Exception as exc:
        logger.exception("checkout: stripe error")
        return _http_response(500, {"error": "Stripe error", "detail": str(exc)})

    logger.info("checkout: created session %s", session.get("id"))
    return _http_response(200, {"session": session})

def stripe_webhook(event: Dict[str, Any]) -> Dict[str, Any]:
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
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "action": "process_webhook",
                    "webhook_event": {
                        "type": event_type,
                        "id": stripe_event.get("id"),
                        "data": json.dumps(stripe_event.get("data", {}).get("object", {}))
                    }
                })
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

def send_email(to_email: str, subject: str, body: str, is_html: bool = False, attachment: Dict[str, Any] = None) -> Dict[str, Any]:
    """Send email using AWS SES with optional attachment"""
    if not SES_FROM_EMAIL:
        return {"success": False, "error": "SES from email not configured"}
    
    try:
        import base64
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        
        ses_client = boto3.client('ses', region_name=AWS_REGION)
        
        if attachment:
            # Use raw email for attachments
            msg = MIMEMultipart()
            msg['Subject'] = subject
            msg['From'] = SES_FROM_EMAIL
            msg['To'] = to_email
            
            msg.attach(MIMEText(body, 'html' if is_html else 'plain'))
            
            # Add attachment
            att = MIMEApplication(base64.b64decode(attachment['content']))
            att.add_header('Content-Disposition', 'attachment', filename=attachment['filename'])
            msg.attach(att)
            
            response = ses_client.send_raw_email(
                Source=SES_FROM_EMAIL,
                Destinations=[to_email],
                RawMessage={'Data': msg.as_string()}
            )
        else:
            # Use simple email without attachments
            message_body = {}
            if is_html:
                message_body['Html'] = {'Data': body}
            else:
                message_body['Text'] = {'Data': body}
            
            response = ses_client.send_email(
                Source=SES_FROM_EMAIL,
                Destination={'ToAddresses': [to_email]},
                Message={
                    'Subject': {'Data': subject},
                    'Body': message_body
                }
            )
        
        return {"success": True, "message": "Email sent successfully", "messageId": response['MessageId']}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_muinmos_token(grant_type: str, client_id: str, client_secret: str, username: str, password: str, api_url: str) -> Dict[str, Any]:
    """Get Muinmos authentication token"""
    if not all([grant_type, client_id, client_secret, username, password, api_url]):
        return {"success": False, "error": "Missing required parameters: grant_type, client_id, client_secret, username, password, api_url"}
    
    # Validate URL format
    if not api_url.startswith(('http://', 'https://')):
        return {"success": False, "error": "api_url must start with http:// or https://"}
    
    try:
        auth_data = {
            "grant_type": grant_type,
            "client_id": client_id,
            "client_secret": client_secret,
            "username": username,
            "password": password
        }
        
        data = urllib.parse.urlencode(auth_data).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            response_body = resp.read().decode("utf-8")
            token_response = json.loads(response_body)
            
        return {"success": True, "token_data": token_response}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Network error: {str(e)} - Check URL: {api_url}"}
    except Exception as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}


def create_assessment(user_email: str, order_code: str, api_url: str, token_type: str, access_token: str) -> Dict[str, Any]:
    """Create Muinmos KYC assessment"""
    if not all([user_email, order_code, api_url, token_type, access_token]):
        return {"success": False, "error": "Missing required parameters"}
    
    try:
        url = f"{api_url}/api/assessment?api-version=2.0"
        
        body_data = {
            "referenceKey": order_code,
            "recipientEmail": user_email,
            "includeRegulatoryTest": False,
            "includeKYCTest": True,
            "kycProfileID": "ede32b82-fcd5-4f17-b3fa-850eb92befc2",
            "includeAdditionalDocs": False,
            "includeSignatures": False,
            "responses": {
                "clientDomicile": "",
                "clientType": "IND",
                "corporationType": "",
                "lei": "",
                "deliveryChannel": "",
                "responseKeyAndValue": {
                    "additionalProp1": "",
                    "additionalProp2": "",
                    "additionalProp3": ""
                }
            }
        }
        
        data = json.dumps(body_data).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"{token_type} {access_token}")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            response_body = resp.read().decode("utf-8")
            
        return {"success": True, "assessment_id": response_body}
    except Exception as e:
        return {"success": False, "error": str(e)}


def muinmos_assessment_search(from_date: str, to_date: str, base_api_url: str, token_type: str, access_token: str) -> Dict[str, Any]:
    """Search Muinmos assessments by date range"""
    if not all([from_date, to_date, base_api_url, token_type, access_token]):
        return {"success": False, "error": "Missing required parameters"}
    
    try:
        from datetime import datetime, timedelta
        
        # Parse dates and adjust by 5 minutes
        from_dt = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
        to_dt = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
        
        adjusted_from = (from_dt - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
        adjusted_to = (to_dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
        
        url = f"{base_api_url}/api/assessment/Search?organisationId=81906526&?api-version=2.0"
        
        body_data = {
            "partyAssessmentId": None,
            "fromDate": adjusted_from,
            "toDate": adjusted_to,
            "referenceKey": None,
            "createdBy": None,
            "respondent": None,
            "pageSize": 9999999,
            "pageNumber": 1
        }
        
        data = json.dumps(body_data).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"{token_type} {access_token}")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            response_body = resp.read().decode("utf-8")
            search_results = json.loads(response_body)
            
        return {"success": True, "data": search_results}
    except Exception as e:
        return {"success": False, "error": str(e)}
