# Phase 4 Runbook: Remote Notifications + Decisions via Telegram

Status: rebuilt for Telegram 2026-07-27, NOT yet tested live. Every step
below needs to be verified live, same as every other phase in this project.

## What this adds

- **One-way progress pushes** to your phone (via a Telegram bot) at each
  pipeline stage: disc detected, rip verified, tagged, Navidrome rescanned,
  and a final complete/issues summary.
- **Remote decisions**: at the two points where `--non-interactive` mode
  used to just auto-retry or fail immediately (whipper rip failure, an
  ambiguous destination folder with exactly 2 candidates), it now sends an
  inline-keyboard push and waits (default 30 min) for your tap before
  falling back to the old default behavior.

## How it works

```
arch-box (ripper_orchestrator.py)
  -> notify.ask() POSTs to Telegram's Bot API (sendMessage with an inline
     keyboard)
  -> Telegram delivers the push to your phone
  -> you tap a button
  -> Telegram records that as a callback_query update
  -> telegram_poller.py (always-on, long-polling Telegram's getUpdates --
     outbound only, no inbound port, no firewall rule needed) picks it up,
     checks it's really from your chat_id, and writes a small JSON file
  -> notify.ask(), which has been polling for that file, picks it up and
     returns the chosen action to the orchestrator
```

Two systemd services on arch-box:
- `cdrip.service` -- oneshot, triggered per-disc by udev (Phase 3).
- `cdrip-telegram-poller.service` -- always-on, needs to be running
  *before* any decision request is sent, since it's what receives
  Telegram's callback.

## Setup

### 1. Create a Telegram bot

In Telegram: message **@BotFather** -> `/newbot` -> follow the prompts.
Copy the token it gives you (looks like `123456789:AAExampleTokenString`).

### 2. Get your chat_id

Send your new bot any message (e.g. "hi"), then in a browser visit:

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Look for `"chat":{"id": <number>, ...}` -- that number is your chat_id.

### 3. Fill in `.env`

```
TELEGRAM_BOT_TOKEN=<token from step 1>
TELEGRAM_CHAT_ID=<id from step 2>
```

No Tailscale IP/hostname needed here -- Telegram's own servers handle
delivery to your phone; arch-box only ever makes outbound HTTPS calls.

### 4. Install the poller

```bash
cd ~/projects/cdrip-pipeline
sudo cp systemd/cdrip-telegram-poller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cdrip-telegram-poller.service
systemctl status cdrip-telegram-poller.service   # should be active (running)
journalctl -u cdrip-telegram-poller.service -f   # should log "waiting for button taps..."
```

No firewall step -- the poller never listens on a port, it only makes
outbound requests to Telegram.

## Test sequence (do these in order, don't skip to the end)

### Step 1: plain notification

```bash
cd ~/projects/cdrip-pipeline
set -a; source .env; set +a
python3 -c "
import logging, notify
log = logging.getLogger('test'); log.addHandler(logging.StreamHandler())
log.setLevel(logging.INFO)
print(notify.notify(log, 'cdrip test: plain notification, no buttons'))
"
```

Confirm the message arrives in Telegram. If it returns `False`, check the
printed warning -- almost always a wrong `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.

### Step 2: decision file mechanics, bypassing Telegram entirely

```bash
mkdir -p ~/cd-rips/decisions
echo '{"action": "RETRY"}' > ~/cd-rips/decisions/test1234.json
cat ~/cd-rips/decisions/test1234.json
```

Just confirms the path `ask()` polls for is right -- nothing to break here
since it's a plain filesystem check.

### Step 3: actionable push end-to-end, including your tap

Make sure `cdrip-telegram-poller.service` is running first (step 4 above),
then:

```bash
python3 -c "
import logging, notify
log = logging.getLogger('test'); log.addHandler(logging.StreamHandler())
log.setLevel(logging.INFO)
cfg = {}
result = notify.ask(log, cfg, 'cdrip test: tap a button', [('YES', 'Yes'), ('NO', 'No')], timeout_seconds=120)
print('Result:', result)
"
```

Tap a button in Telegram within 2 minutes. Confirm the script prints
`Result: YES` (or `NO`) instead of timing out with `Result: None`. If it
times out, check in order: did the message arrive with buttons at all? did
tapping it show a brief "Got it: ..." toast in Telegram (that's
`answerCallbackQuery` firing)? does
`journalctl -u cdrip-telegram-poller.service -f` show "Decision recorded"
when you tap?

### Step 4: full pipeline test

Once steps 1-3 all work, the wiring in `rip()`/`find_dest_folder()` should
work automatically the next time `--non-interactive` hits one of those
decision points -- nothing further to install for that part. Worth
deliberately forcing one of those paths once (e.g. a disc you know isn't in
MusicBrainz, to trigger the whipper-failure retry decision) to see the full
loop fire for real.

## Known gaps / things I couldn't verify from here

- No retry/backoff if Telegram's API is briefly unreachable when
  `notify()`/`ask()` fires -- it just logs a warning and the pipeline falls
  back to old default behavior.
- `telegram_poller.py`'s offset file (`~/cd-rips/decisions/.telegram_offset`)
  persists across restarts so old updates won't be reprocessed, but if it
  crashes mid-batch before saving, a handful of updates could be
  reprocessed -- harmless, since writing the same decision file twice is a
  no-op and calling `answerCallbackQuery` on an already-answered tap just
  errors quietly (caught, logged, ignored).
- Only one `chat_id` is trusted. If you ever want a second phone/person able
  to respond, that'll need to become a list instead of a single value.
- Telegram's own reliability/latency for delivering pushes and processing
  button taps hasn't been tested live yet -- start with Step 1 before
  touching anything else.
