#!/usr/bin/env python3
"""Home Assistant Companion App push notifications + remote decision relay
for cdrip-pipeline (Phase 4). NOT YET TESTED LIVE -- see docs/phase4-runbook.md
for the Home Assistant-side config and the full test sequence.

Requires in the environment (see .env.example):
    HA_URL                Home Assistant base URL, e.g. a Tailscale MagicDNS
                          hostname like http://homeassistant.tailnet-name.ts.net:8123
    HA_TOKEN              Long-lived access token: HA profile (bottom-left) ->
                          Security tab -> "Long-lived access tokens" -> Create.
    HA_MOBILE_TARGET      The exact notify service name for your phone, e.g.
                          mobile_app_kevins_iphone. Find it in HA under
                          Developer Tools -> Actions, search "notify".
    CDRIP_DECISION_SECRET Shared secret the decision_server.py callback must
                          present (defense-in-depth on top of Tailscale-only
                          network exposure -- see decision_server.py).

Design: a "remote decision" is a choice the pipeline used to make by exiting
or auto-retrying under --non-interactive (e.g. "whipper failed, retry with
--unknown?"). Instead, when HA_URL/HA_TOKEN/HA_MOBILE_TARGET are all set:
  1. ask() sends an actionable push via the HA Companion App. Each action's
     identifier is encoded as "<ACTION_ID>|<request_id>" so the Home
     Assistant automation that relays the tap back doesn't need to track any
     state of its own -- see the automation YAML in docs/phase4-runbook.md.
  2. ask() then polls a local directory for a response file that
     decision_server.py writes once Home Assistant relays the tapped action
     back over the tailnet.
  3. If nothing comes back within the timeout, ask() returns None so the
     pipeline can fall back to its old non-interactive behavior instead of
     hanging forever waiting on a phone that's asleep or out of signal.

If the HA env vars aren't set at all, notify()/ask() degrade gracefully:
notify() just logs a warning and returns False, ask() returns None
immediately -- callers should already have a sensible fallback for that
(same as before Phase 4 existed).
"""
import json
import os
import time
import uuid

try:
    import requests
except ImportError:
    requests = None


def _ha_settings():
    return (
        os.environ.get("HA_URL"),
        os.environ.get("HA_TOKEN"),
        os.environ.get("HA_MOBILE_TARGET"),
    )


def notify(log, message, title="cdrip-pipeline", actions=None):
    """Send a plain (or actionable) push via the HA Companion App.

    Best-effort: logs a warning and returns False on any failure rather than
    raising, since a notification failure should never take down the actual
    rip.
    """
    ha_url, ha_token, ha_target = _ha_settings()
    if not (ha_url and ha_token and ha_target):
        log.warning(f"HA notifications not configured (HA_URL/HA_TOKEN/"
                    f"HA_MOBILE_TARGET) -- skipping: {message}")
        return False
    if requests is None:
        log.warning("`requests` not installed -- cannot send HA notification.")
        return False

    payload = {"message": message, "title": title}
    if actions:
        payload["data"] = {"actions": actions}

    url = f"{ha_url.rstrip('/')}/api/services/notify/{ha_target}"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning(f"HA notification failed: {e}")
        return False


def ask(log, cfg, message, choices, timeout_seconds=1800, poll_interval=5):
    """Send an actionable push (choices: list of (action_id, label) tuples,
    max 3 -- that's the practical limit iOS shows) and wait for a tap to be
    relayed back via decision_server.py + a Home Assistant automation.

    Returns the chosen action_id, or None if notifications aren't
    configured, sending failed, or nothing came back within the timeout.

    NOT YET TESTED LIVE.
    """
    request_id = uuid.uuid4().hex[:8]
    actions = [
        {"action": f"{action_id}|{request_id}", "title": label}
        for action_id, label in choices
    ]

    sent = notify(log, message, actions=actions)
    if not sent:
        return None

    decisions_dir = os.path.expanduser(
        cfg.get("decisions_dir", "~/cd-rips/decisions")
    )
    os.makedirs(decisions_dir, exist_ok=True)
    response_path = os.path.join(decisions_dir, f"{request_id}.json")

    log.info(
        f"Sent remote decision request {request_id} -- waiting up to "
        f"{timeout_seconds}s for a response (check your phone): {message}"
    )
    waited = 0
    while waited < timeout_seconds:
        if os.path.exists(response_path):
            try:
                with open(response_path) as f:
                    data = json.load(f)
                action = data.get("action")
            finally:
                os.remove(response_path)
            log.info(f"Got remote decision for {request_id}: {action}")
            return action
        time.sleep(poll_interval)
        waited += poll_interval

    log.warning(f"No response to request {request_id} after {timeout_seconds}s. "
                "Timing out and falling back to default behavior.")
    notify(log, f"No response received in time for: {message}")
    return None
