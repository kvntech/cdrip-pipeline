# Phase 4 Runbook: Remote Notifications + Decisions via Home Assistant

Status: built 2026-07-27, NOT yet tested live. This spans three systems
(arch-box, Home Assistant, your iPhone) that I can't test end-to-end myself
-- every step below needs to be verified live, same as every other phase in
this project.

## What this adds

- **One-way progress pushes** to your phone (via the Home Assistant
  Companion App) at each pipeline stage: disc detected, rip verified,
  tagged, Navidrome rescanned, and a final complete/issues summary.
- **Remote decisions**: at the two points where `--non-interactive` mode
  used to just auto-retry or fail immediately (whipper rip failure, an
  ambiguous destination folder with exactly 2 candidates), it now sends an
  actionable push with tappable buttons and waits (default 30 min) for your
  response before falling back to the old default behavior.

## How it works (so debugging makes sense if something breaks)

```
arch-box (ripper_orchestrator.py)
  -> ha_notify.ask() POSTs to Home Assistant's REST API
     (notify.<your_device> service, with actions=[...])
  -> Home Assistant Companion App shows the push with buttons on your iPhone
  -> you tap a button
  -> iOS sends the action back to HA, which fires a
     "mobile_app_notification_action" event
  -> a Home Assistant automation (you configure this, see below) catches
     that event and calls a rest_command back to arch-box
  -> decision_server.py (always-on, separate from the per-disc
     cdrip.service) receives that POST and writes a small JSON file
  -> ha_notify.ask(), which has been polling for that file, picks it up and
     returns the chosen action to the orchestrator
```

Two separate systemd services are involved on arch-box:
- `cdrip.service` -- oneshot, triggered per-disc by udev (Phase 3).
- `cdrip-decision-server.service` -- always-on, needs to be running *before*
  any decision request is sent, since it's what receives Home Assistant's
  callback.

## Setup

### 1. Home Assistant long-lived access token
In Home Assistant: click your profile (bottom-left) -> Security tab ->
"Long-lived access tokens" -> Create Token. Copy it immediately, it's only
shown once.

### 2. Find your exact notify service name
In Home Assistant: Developer Tools -> Actions (or "Services" on older
versions), search for "notify". You're looking for something like
`notify.mobile_app_kevins_iphone` -- the exact name depends on what your
phone is named in HA. Use everything after `notify.` as `HA_MOBILE_TARGET`.

### 3. Fill in `.env`
```
HA_URL=http://<your-ha-tailscale-hostname-or-ip>:8123
HA_TOKEN=<the token from step 1>
HA_MOBILE_TARGET=mobile_app_<your_device>
CDRIP_DECISION_SECRET=<generate with: openssl rand -hex 20>
```
`HA_URL` should be reachable from arch-box -- if Home Assistant is also on
your tailnet, its Tailscale MagicDNS name works well here so this isn't
dependent on your home LAN being up.

### 4. Home Assistant side: rest_command + automation
Add to Home Assistant's `configuration.yaml` (or split into a packages file
if you use those) -- **use the same secret you put in `CDRIP_DECISION_SECRET`**:

```yaml
rest_command:
  cdrip_decision:
    url: "http://<arch-box-tailscale-ip>:8420/decision"
    method: POST
    content_type: "application/json"
    headers:
      X-Cdrip-Secret: "<same value as CDRIP_DECISION_SECRET in .env>"
    payload: >
      {"request_id": "{{ request_id }}", "action": "{{ action }}"}
```

Then an automation (Settings -> Automations -> Create -> "Edit in YAML"),
triggered whenever you tap one of the action buttons:

```yaml
alias: "cdrip-pipeline: relay notification action"
trigger:
  - platform: event
    event_type: mobile_app_notification_action
condition:
  - condition: template
    value_template: >
      {{ '|' in trigger.event.data.action }}
action:
  - service: rest_command.cdrip_decision
    data:
      action: "{{ trigger.event.data.action.split('|')[0] }}"
      request_id: "{{ trigger.event.data.action.split('|')[1] }}"
mode: parallel
```

The `action` string is always `"<ACTION_ID>|<request_id>"` (see
`ha_notify.py`'s `ask()`), so this automation just splits it apart and
forwards both halves -- it doesn't need to track any state of its own.

### 5. Install and firewall the decision server
```bash
cd ~/projects/cdrip-pipeline
sudo cp systemd/cdrip-decision-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cdrip-decision-server.service
systemctl status cdrip-decision-server.service   # should be active (running)
```

Restrict port 8420 to your Tailscale interface. Example using `nftables`
(adjust if you're using something else -- check `sudo nft list ruleset` or
`sudo ufw status` first to see what's actually active on arch-box, since I
don't know which one you're running):

```bash
sudo nft add rule inet filter input iifname "tailscale0" tcp dport 8420 accept
sudo nft add rule inet filter input tcp dport 8420 drop
```

Verify: from a machine NOT on your tailnet, `curl http://<arch-box-tailscale-ip>:8420/decision`
should fail to connect; from Home Assistant (assuming it's tailnet-reachable
too), it should at least get a response (400, since it's a bare GET with no
body -- that still proves the port is reachable).

## Test sequence (do these in order, don't skip to the end)

### Step 1: test a plain notification, no actions
```bash
cd ~/projects/cdrip-pipeline
set -a; source .env; set +a
python3 -c "
import logging, ha_notify
log = logging.getLogger('test'); log.addHandler(logging.StreamHandler())
log.setLevel(logging.INFO)
print(ha_notify.notify(log, 'cdrip test: plain notification, no buttons'))
"
```
Confirm the push arrives on your iPhone. If `notify()` returns `False`,
check the printed warning -- almost always a wrong `HA_URL`/`HA_TOKEN`/
`HA_MOBILE_TARGET` at this stage.

### Step 2: test the decision server directly, bypassing Home Assistant
```bash
curl -X POST http://localhost:8420/decision \
  -H "X-Cdrip-Secret: <your CDRIP_DECISION_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"request_id": "test1234", "action": "RETRY"}'
cat ~/cd-rips/decisions/test1234.json   # should show {"action": "RETRY"}
```
This confirms the server + secret check + file-writing all work, with Home
Assistant out of the loop entirely.

### Step 3: test an actionable push end-to-end, including your tap
```bash
python3 -c "
import logging, ha_notify
log = logging.getLogger('test'); log.addHandler(logging.StreamHandler())
log.setLevel(logging.INFO)
cfg = {}
result = ha_notify.ask(log, cfg, 'cdrip test: tap a button', [('YES', 'Yes'), ('NO', 'No')], timeout_seconds=120)
print('Result:', result)
"
```
Tap a button on your phone within 2 minutes. Confirm: the script prints
`Result: YES` (or `NO`) instead of timing out with `Result: None`. If it
times out, check in order: did the push arrive with buttons at all? did
tapping it show up in Home Assistant's Logbook as a
`mobile_app_notification_action` event? did the automation's trace show it
firing and calling `rest_command.cdrip_decision` successfully? did
`journalctl -u cdrip-decision-server.service` show the incoming POST?

### Step 4: full pipeline test
Once steps 1-3 all work, the wiring in `rip()`/`find_dest_folder()` should
work automatically the next time `--non-interactive` hits one of those
decision points -- there's nothing further to install for that part. Worth
deliberately forcing one of those paths once (e.g. a disc you know isn't in
MusicBrainz, to trigger the whipper-failure retry decision) to see the full
loop fire for real.

## Known gaps / things I couldn't verify from here

- The exact JSON shape Home Assistant expects for the Companion App's
  `actions` data structure has shifted across HA versions before -- if
  step 1 works but action buttons in step 3 don't show up on the phone
  (message arrives, but no buttons), check current Companion App docs for
  the `data.actions` format on your installed HA version.
- iOS notification action button counts and exact behavior (e.g. whether
  tapping works from the lock screen vs. requiring the app open) can vary by
  iOS version -- test this for real above.
- No retry/backoff if `HA_URL` is briefly unreachable (e.g. HA restarting)
  when `notify()`/`ask()` fires -- it just logs a warning and the pipeline
  falls back to old behavior. Fine for now; revisit if this turns out to be
  flaky in practice.
- `decision_server.py` has no rate limiting -- not a real concern on a
  Tailscale-only endpoint with a shared secret, but noting it since it's a
  write endpoint.
