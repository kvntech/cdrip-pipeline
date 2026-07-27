# Phase 3 Runbook: Hands-off Automation (udev + systemd)

Status: CONFIRMED WORKING LIVE 2026-07-27 (Das EFX - Dead Serious), triggered
by an actual physical disc insertion end-to-end: udev -> systemd ->
full pipeline -> Navidrome, completely unattended. Three real bugs were
found and fixed during this testing -- see "Live test results" below. None
of them were visible during manual/interactive testing; all three only
surfaced under a genuine unattended systemd run.

## What this adds

A udev rule (`udev/99-cdrip.rules`) that fires when an audio CD is inserted
into `/dev/sr0`, triggering a systemd oneshot service (`systemd/cdrip.service`)
that runs `ripper_orchestrator.py --non-interactive` with no human in the
loop.

`--non-interactive` (new orchestrator flag) changes behavior at every point
that used to prompt:
- Skips the "insert CD, press Enter" wait.
- If whipper fails, auto-retries once with `--unknown` instead of asking.
- If the staging folder is ambiguous after ripping, exits with an error
  instead of asking which one to use.
- Runs `beet -q import` (quiet mode) instead of an interactive import.
- If the destination folder is ambiguous or missing, exits with an error
  instead of asking.
- Skips the "delete staging folder?" confirmation.

The design intent: never block on a TTY that doesn't exist. Where a human
judgment call would normally happen, fail loudly and leave things in a
recoverable state (staging not cleaned up, clear error in the log) rather
than hang forever or silently guess wrong.

## Prerequisites before installing

1. Phase 2 orchestrator confirmed working across 4 live discs, MusicBrainz
   matching fixed, Navidrome rescan verified (see README.md) -- all done as
   of 2026-07-27.
2. **Not yet done:** add to `~/.config/beets/config.yaml` under `import:`:
   ```
   import:
     quiet_fallback: asis
     resume: yes   # was 'ask' -- 'ask' would hang forever with no TTY
   ```
   `quiet_fallback: asis` matters specifically for `-q` (quiet) mode: without
   it, an ambiguous match under quiet mode defaults to `skip`, which would
   silently drop an album from the library rather than tagging it with
   whipper's own tags. **This has only been reasoned about, not tested
   against a real ambiguous-match disc.** Test it (see below) before trusting
   it on a disc you can't easily re-rip.
3. `.env` (untracked; see `.env.example`) has real `NAVIDROME_USER`/`NAVIDROME_PASS`.

## Install

    sudo cp udev/99-cdrip.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo cp systemd/cdrip.service /etc/systemd/system/
    sudo systemctl daemon-reload

## Step 1: test `--non-interactive` manually, without udev involved

Insert a disc you don't mind re-ripping, then run exactly what systemd will
run, watching the output directly instead of through the journal:

    cd ~/cdrip-pipeline
    set -a; source .env; set +a
    python3 ripper_orchestrator.py --non-interactive

Confirm it completes cleanly end-to-end (rip, tag, art, Navidrome rescan,
staging cleanup) with zero prompts and a clean exit. If it hangs or exits
non-zero anywhere, fix that before moving on -- don't debug udev and the
orchestrator's non-interactive logic at the same time.

## Step 2: confirm the udev event actually looks like we expect

    udevadm monitor --udev --subsystem-match=block

In another terminal (or physically), insert/eject a disc. Confirm you see a
`change` event with `ID_CDROM=1` and an empty `ID_FS_TYPE`. If the real event
looks different on arch-box's drive, the match conditions in
`99-cdrip.rules` need adjusting before it'll ever fire.

## Step 3: confirm the systemd service actually starts from udev

    journalctl -u cdrip.service -f

In another terminal, insert a disc. Confirm `cdrip.service` starts (you'll
see it in the journal) and the orchestrator runs to completion. Do NOT
`systemctl enable cdrip.service` -- it's meant to be started only by the udev
rule's `SYSTEMD_WANTS`, not at boot.

## Live test results (2026-07-27)

Real disc: Das EFX - Dead Serious. Sequence: physical disc insertion ->
udev rule fired (`change` event, `ID_CDROM=1`, empty `ID_FS_TYPE`, confirmed
via `udevadm monitor`) -> `SYSTEMD_WANTS=cdrip.service` applied to the device
(confirmed via `udevadm info --query=all`) -> `cdrip.service` started ->
full pipeline completed unattended (rip, AccurateRip verify, beets tag,
log/cue copy, art fetch, Navidrome rescan, staging cleanup).

Three real bugs surfaced during this testing, none of them visible in any
prior interactive/manual test:

1. **Wrong repo path in `cdrip.service`.** Unit file assumed
   `/home/kevin/cdrip-pipeline`; actual clone is at
   `/home/kevin/projects/cdrip-pipeline`. Fixed in both the installed copy
   and the repo's `systemd/cdrip.service`.
2. **`rip()`'s folder-detection broke on the second-ever rip.** It diffed
   `staging_dir`'s top-level contents before/after the rip looking for a
   "new" folder, but whipper's wrapper folder (e.g. `album`) gets reused,
   not recreated, across runs. Fixed: search the whole staging tree for
   whichever folder contains `.flac` files with the newest mtime.
3. **`sudo` requires a TTY that doesn't exist under systemd.** Every
   `sudo`-prefixed command (`beet import`, `beet fetchart`, log/cue `cp`)
   failed instantly with `sudo: a terminal is required to read the
   password`. Fixed with a scoped `NOPASSWD` sudoers entry for exactly
   `/usr/bin/beet` and `/usr/bin/cp`:
   ```
   kevin ALL=(root) NOPASSWD: /usr/bin/beet, /usr/bin/cp
   ```
   installed via `sudo visudo -f /etc/sudoers.d/cdrip-pipeline` (never edit
   `/etc/sudoers` directly). Verify with `sudo -n beet version` -- the `-n`
   flag mimics exactly what systemd hits (fails immediately rather than
   prompting), so it can't give a false pass from a cached terminal
   credential the way a plain `sudo beet version` could.

A disc that got stranded mid-testing (ripped and AccurateRip-verified, but
not yet tagged) was recovered with `resume_rip.py <album-folder-path>`,
which reuses the orchestrator's own `import_beets()` onward rather than
doing anything by hand differently. Worth keeping around -- this situation
(interrupted after a successful rip) will happen again.

## Known gaps (carry into Phase 4 or fix here first)

- `quiet_fallback: asis` behavior under `-q` has only been reasoned about,
  not yet tested against a real ambiguous-match disc (both live test discs
  so far got strong/automatic matches).
- No handling for re-inserting a disc that's already been ripped -- would
  attempt a duplicate rip. Consider a lockfile or a check-before-rip step.
- Failure notification is now covered by Phase 4 (Home Assistant push
  notifications + remote decisions) -- see docs/phase4-runbook.md.
- Only tested against a single drive (`/dev/sr0`, hardcoded in
  config.yaml). Multiple drives would need per-drive config and a templated
  systemd unit (`cdrip@.service` keyed on device name) instead of the current
  single fixed unit.
