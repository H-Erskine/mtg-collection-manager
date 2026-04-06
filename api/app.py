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

from flask import Flask, request, abort
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

_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")


def _validate_twilio(req) -> bool:
    """Return True if the request carries a valid Twilio signature."""
    if not _AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN not set — skipping signature validation")
        return True
    validator = RequestValidator(_AUTH_TOKEN)
    signature = req.headers.get("X-Twilio-Signature", "")
    return validator.validate(req.url, req.form, signature)


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
    return str(resp), 200, {"Content-Type": "text/xml"}


@app.route("/health", methods=["GET"])
def health():
    """Simple health check for load balancers / uptime monitors."""
    return {"status": "ok"}, 200
