#!/usr/bin/env python3
"""Tiny always-on HTTP server that receives decision callbacks from Home
Assistant (via an automation triggered on mobile_app_notification_action,
calling a rest_command -- see docs/phase4-runbook.md) and writes them where
ha_notify.ask() is polling for them.

NOT YET TESTED LIVE.

Intentionally stdlib-only (http.server), no new dependency, since this runs
as its own always-on systemd service (cdrip-decision-server.service),
separate from the per-disc cdrip.service which only runs when a disc is
inserted.

Security model: this is a write-only endpoint that just drops a JSON file on
disk, but it should still not be reachable from the open internet.
Recommended setup:
  1. Bind to 0.0.0.0 (default) but firewall the port to your Tailscale
     interface only (see docs/phase4-runbook.md for an nftables example) --
     don't rely on the shared secret alone.
  2. Set CDRIP_DECISION_SECRET in the environment and configure the same
     value in Home Assistant's rest_command headers, so a request without
     the correct header is rejected even if something reaches this port.

Usage:
    python3 decision_server.py [--port 8420] [--decisions-dir ~/cd-rips/decisions]
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    decisions_dir = os.path.expanduser("~/cd-rips/decisions")
    shared_secret = os.environ.get("CDRIP_DECISION_SECRET")

    def _reject(self, code, message):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(message.encode())

    def do_POST(self):
        if self.path != "/decision":
            self._reject(404, "not found")
            return

        if self.shared_secret:
            provided = self.headers.get("X-Cdrip-Secret")
            if provided != self.shared_secret:
                self._reject(403, "bad secret")
                return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            request_id = data["request_id"]
            action = data["action"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self._reject(400, f"bad request: {e}")
            return

        # Guard against a crafted request_id used for path traversal --
        # ha_notify.py only ever generates short alnum hex ids, so anything
        # else is either a bug or someone poking at the endpoint.
        safe_id = "".join(c for c in request_id if c.isalnum())
        if not safe_id:
            self._reject(400, "invalid request_id")
            return

        os.makedirs(self.decisions_dir, exist_ok=True)
        out_path = os.path.join(self.decisions_dir, f"{safe_id}.json")
        with open(out_path, "w") as f:
            json.dump({"action": action}, f)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        # print() -> journal under systemd, same as everything else here.
        print(f"{self.address_string()} - {format % args}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--decisions-dir", default=os.path.expanduser("~/cd-rips/decisions")
    )
    parser.add_argument(
        "--bind",
        default="0.0.0.0",
        help="Bind address. Firewall this port to your Tailscale interface "
             "only -- see docs/phase4-runbook.md.",
    )
    args = parser.parse_args()

    Handler.decisions_dir = args.decisions_dir
    os.makedirs(args.decisions_dir, exist_ok=True)

    if not Handler.shared_secret:
        print("WARNING: CDRIP_DECISION_SECRET is not set in the environment -- "
              "this endpoint will accept requests with no shared-secret check "
              "at all. Set it (and configure the same value in Home Assistant's "
              "rest_command) before relying on this over anything but a fully "
              "trusted network.")

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"cdrip decision server listening on {args.bind}:{args.port}, "
          f"writing to {args.decisions_dir}")
    server.serve_forever()


if __name__ == "__main__":
    main()
