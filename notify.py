#!/usr/bin/env python3
"""
Phase 4: Telegram bot notifications + remote decisions for cdrip-pipeline.

Setup:
  1. Message @BotFather on Telegram, /newbot, follow the prompts -> get a
     bot token (looks like 123456789:AAExampleTokenString).
  2. Send your new bot any message (e.g. "hi") so it has a chat to reply in.
  3. Visit https://api.telegram.org/bot<TOKEN>/getUpdates in a browser right
     after step 2 -- look for "chat":{"id": <number>, ...}. That number is
     your chat_id.
  4. Put both in .env:
       TELEGRAM_BOT_TOKEN=<token from step 1>
       TELEGRAM_CHAT_ID=<id from step 3>

notify() sends a one-way message. ask() sends a message with inline keyboard
buttons and waits (polling a local file written by telegram_poller.py) for
a tap, up to timeout_seconds.
"""
import json
import logging
import os
import time
import uuid
from pathlib import Path

import requests

API_BASE = "https://api.telegram.org/bot{token}/{method}"
DECISIONS_DIR = Path.home() / "cd-rips" / "decisions"


def _config():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def notify(log: logging.Logger, message: str) -> bool:
    """Send a one-way push. Returns True on success, False (logged) on failure."""
    token, chat_id = _config()
    if not token or not chat_id:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
                    f"missing). Skipping notify: {message}")
        return False
    try:
        resp = requests.post(
            API_BASE.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning(f"Telegram notify failed: {e}")
        return False


def ask(log: logging.Logger, cfg, message: str, actions, timeout_seconds: int = 1800):
    """
    Send an actionable push with inline keyboard buttons and wait for a tap.

    actions: list of (ACTION_ID, label) tuples, e.g.
             [("RETRY", "Retry with --unknown"), ("GIVE_UP", "Give up")]

    Returns the tapped ACTION_ID (str), or None if it timed out, failed to
    send, or Telegram isn't configured.
    """
    token, chat_id = _config()
    if not token or not chat_id:
        log.warning(f"Telegram not configured, cannot ask for a remote decision: {message}")
        return None

    request_id = uuid.uuid4().hex[:12]
    keyboard = {
        "inline_keyboard": [[
            {"text": label, "callback_data": f"{action_id}|{request_id}"}
            for action_id, label in actions
        ]]
    }
    try:
        resp = requests.post(
            API_BASE.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": message, "reply_markup": keyboard},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Telegram ask() failed to send: {e}")
        return None

    log.info(f"Waiting up to {timeout_seconds}s for a decision (request_id={request_id})...")
    decision_file = DECISIONS_DIR / f"{request_id}.json"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if decision_file.exists():
            try:
                return json.loads(decision_file.read_text()).get("action")
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Could not read decision file {decision_file}: {e}")
                return None
        time.sleep(3)

    log.warning(f"No response within {timeout_seconds}s for request_id={request_id}.")
    return None
