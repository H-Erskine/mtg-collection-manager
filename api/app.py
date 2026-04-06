"""
WhatsApp webhook server for MTG Manager.

Receives messages via Twilio, routes them to handlers, and replies.

Environment variables required:
  TWILIO_AUTH_TOKEN  — from your Twilio console (used to validate requests)

Run locally for testing:
  gunicorn -w 1 -b 127.0.0.1:5000 api.app:app

Or with Flask dev server (not for production):
  flask --app api.app run
"""
import os
import logging

from dotenv import load_dotenv
from flask import Flask, request, abort
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from .handlers import (
    handle_boxes,
    handle_build,
    handle_help,
    handle_missing,
    handle_sync,
    handle_unbox,
    HELP_TEXT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Trust one layer of proxy headers (ngrok / nginx) so Flask reconstructs
# the correct public HTTPS URL for Twilio signature validation.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
_SKIP_VALIDATION = os.environ.get("SKIP_TWILIO_VALIDATION", "").lower() in ("1", "true", "yes")


def _validate_twilio(req) -> bool:
    """Return True if the request carries a valid Twilio signature."""
    if _SKIP_VALIDATION:
        logger.warning("Twilio signature validation DISABLED (SKIP_TWILIO_VALIDATION=true)")
        return True
    if not _AUTH_TOKEN:
        logger.error("TWILIO_AUTH_TOKEN not set — refusing all requests")
        abort(500)
    validator = RequestValidator(_AUTH_TOKEN)
    signature = req.headers.get("X-Twilio-Signature", "")
    # Twilio always signs against the public HTTPS URL. Force https here
    # because proxies (ngrok, nginx) may forward the request as http internally.
    url = req.url.replace("http://", "https://", 1)
    logger.info("Validating against URL: %s", url)
    result = validator.validate(url, req.form, signature)
    if not result:
        logger.warning("Signature validation FAILED for URL: %s", url)
    return result


def _route(body: str) -> str:
    """Parse a plain-text WhatsApp message and dispatch to the right handler."""
    parts = body.strip().split()
    if not parts:
        return handle_help()

    cmd = parts[0].lower()

    # help
    if cmd == "help":
        return handle_help()

    # sync [color_group]
    if cmd == "sync":
        color_group = parts[1] if len(parts) > 1 else None
        return handle_sync(color_group)

    # boxes
    if cmd == "boxes":
        return handle_boxes()

    # unbox <deck_id>
    if cmd == "unbox":
        if len(parts) < 2:
            return "Usage: unbox <deck_id>\nSend 'boxes' to see deck IDs."
        return handle_unbox(parts[1])

    # missing <url> [-m <n>]
    if cmd == "missing":
        if len(parts) < 2:
            return "Usage: missing <url> [-m <min_variants>]"
        url = parts[1]
        min_variants = 1
        sideboard = False
        i = 2
        while i < len(parts):
            if parts[i] == "-m" and i + 1 < len(parts):
                try:
                    min_variants = int(parts[i + 1])
                except ValueError:
                    return f"Invalid value for -m: '{parts[i + 1]}' (must be a number)"
                i += 2
            elif parts[i] == "--sideboard":
                sideboard = True
                i += 1
            else:
                i += 1
        return handle_missing(url, sideboard=sideboard, min_variants=min_variants)

    # build <url> box <name>
    if cmd == "build":
        # Expected: build <url> box <box_name...>
        if len(parts) < 4 or "box" not in [p.lower() for p in parts[2:]]:
            return "Usage: build <url> box <box_name>\nExample: build https://... box White Box"
        url = parts[1]
        try:
            box_kw_index = next(
                i for i, p in enumerate(parts) if i >= 2 and p.lower() == "box"
            )
        except StopIteration:
            return "Usage: build <url> box <box_name>"
        box_name = " ".join(parts[box_kw_index + 1:])
        if not box_name:
            return "Please provide a box name after 'box'."
        sideboard = "--sideboard" in parts
        return handle_build(url, box=box_name, sideboard=sideboard)

    return f"Unknown command: '{cmd}'\n\n{HELP_TEXT}"


@app.route("/webhook", methods=["POST"])
def webhook():
    if not _validate_twilio(request):
        logger.warning("Invalid Twilio signature from %s", request.remote_addr)
        abort(403)

    body = request.form.get("Body", "").strip()
    sender = request.form.get("From", "unknown")
    logger.info("Message from %s: %s", sender, body[:120])

    reply = _route(body)

    # WhatsApp messages have a 1600-char limit; truncate gracefully if needed
    if len(reply) > 1550:
        reply = reply[:1500] + "\n\n[Message truncated — use -m to filter results]"

    resp = MessagingResponse()
    resp.message(reply)
    twiml = str(resp)
    logger.info("TwiML response: %s", twiml)
    return twiml, 200, {"Content-Type": "text/xml"}


@app.route("/health", methods=["GET"])
def health():
    """Simple health check for load balancers / uptime monitors."""
    return {"status": "ok"}, 200
