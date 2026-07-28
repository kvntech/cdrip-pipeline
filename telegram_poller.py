#!/usr/bin/env python3
"""
Phase 4: always-on Telegram long-poller for cdrip-pipeline.

Runs independently of any single rip (systemd: cdrip-telegram-poller.service).
Long-polls Telegram's getUpdates endpoint -- outbound only, no inbound port,
no firewall rule needed -- for callback_query updates (button taps), matches
them to pending requests by request_id, and writes the chosen action to
~/cd-rips/decisions/<request_id>.json for notify.ask() to pick up.

Only accepts taps from the chat_id in TELEGRAM_CHAT_ID -- ignores anything
else, in case the bot token ever leaks.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

API_BASE = "https://api.telegram.org/bot{token}/{method}"
DECISIONS_DIR = Path.home() / "cd-rips" / "decisions"
OFFSET_FILE = DECISIONS_DIR / ".telegram_offset"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                     stream=sys.stdout)
log = logging.getLogger("telegram_poller")


def load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_offset(offset: int):
    OFFSET_FILE.write_text(str(offset))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. Exiting.")
        sys.exit(1)

    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    offset = load_offset()
    log.info("Telegram poller started, waiting for button taps...")

    while True:
        try:
            resp = requests.get(
                API_BASE.format(token=token, method="getUpdates"),
                params={"offset": offset, "timeout": 30,
                        "allowed_updates": '["callback_query"]'},
                timeout=35,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except requests.RequestException as e:
            log.warning(f"getUpdates failed, retrying in 5s: {e}")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            cq = update.get("callback_query")
            if not cq:
                continue

            from_id = str(cq.get("from", {}).get("id", ""))
            if from_id != str(chat_id):
                log.warning(f"Ignoring callback_query from unrecognized chat_id={from_id}")
                continue

            data = cq.get("data", "")
            if "|" not in data:
                log.warning(f"Ignoring malformed callback_data: {data!r}")
                continue
            action, request_id = data.split("|", 1)

            (DECISIONS_DIR / f"{request_id}.json").write_text(json.dumps({"action": action}))
            log.info(f"Decision recorded: request_id={request_id} action={action}")

            try:
                requests.post(
                    API_BASE.format(token=token, method="answerCallbackQuery"),
                    json={"callback_query_id": cq["id"], "text": f"Got it: {action}"},
                    timeout=10,
                )
            except requests.RequestException as e:
                log.warning(f"answerCallbackQuery failed (non-fatal): {e}")

        save_offset(offset)


if __name__ == "__main__":
    main()
