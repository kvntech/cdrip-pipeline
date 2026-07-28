# cdrip-pipeline

Automated CD ripping pipeline for my homelab. Insert a CD into the external drive, walk away, come back to a tagged FLAC album in Navidrome.

## Goals

- High-quality archival FLAC rips (AccurateRip-verified)
- Automatic MusicBrainz tagging
- Hands-off operation: insert disc, eject when done
- Final library accessible via Navidrome

## Architecture

```
[USB CD drive] -> [whipper] -> [staging] -> [beets] -> [NAS] -> [Navidrome]
                       ^
                       |
                [Python orchestrator]
                       ^
                       |
                  [udev trigger]
```

### Components

- **Ripping:** whipper (Python wrapper around cdparanoia, AccurateRip-verified)
- **Tagging:** beets (MusicBrainz lookup, tag normalization, file moves) + explicit fetchart/embedart step
- **Orchestration:** Python script wrapping whipper + beets (`ripper_orchestrator.py`, this project) — confirmed working end-to-end across 3 live rips
- **Trigger:** udev rule on `/dev/sr0` insertion -> systemd service -> orchestrator — confirmed working end-to-end live
- **Notifications:** Telegram bot (`notify.py` + `telegram_poller.py`) — milestone push notifications at each pipeline stage, plus tap-to-decide remote buttons for whipper rip failures and ambiguous destination folders
- **Library:** Navidrome (LXC 106) scanning the NAS music library

### Hardware

- **Host:** arch-box (EndeavourOS, ThinkCentre M700 Tiny, 192.168.8.6)
- **Drive:** LG GP65NB60 (external USB DVD drive)
- **NAS:** TrueNAS (192.168.8.20), tank pool (SSD)

### Paths

- **Staging (local on arch-box):** `~/cd-rips/staging/`
- **NFS mountpoint (confirmed):** `/mnt/tank/music` — NOT `/mnt/tank/music/library`,
  which is a subfolder inside the mount, not the mount itself. This distinction
  matters for anything that checks mount status (see Known Issues).
- **Library (NFS from TrueNAS):** `/mnt/tank/music/library/FLAC/CDRips/`
- **Navidrome scan root:** `/mnt/tank/music/library/`

### File layout

Single disc:
```
Artist/YYYY - Album/NN - Artist - Title.flac
```

Multi-disc:
```
Artist/YYYY - Album/D-NN - Artist - Title.flac
```

Compilations:
```
Compilations/YYYY - Album/NN - Artist - Title.flac
```

## Phases

- [x] **Phase 0:** Manual end-to-end. Install whipper + beets, mount NFS, characterize drive, rip one test CD manually.
  - [x] GitHub repo + initial README
  - [x] NFS export from TrueNAS (Maproot=apps)
  - [x] NFS mount on arch-box with systemd automount fstab entry
  - [x] CDRips directory structure on NAS
  - [x] Whipper installed (Arch package)
  - [x] LG GP65NB60 characterized: cache defeat OK, read offset +6 (empirically verified, not in AccurateRip drive DB)
  - [x] Beets installed (pacman) + beets-extrafiles (pip, system-wide) — extrafiles later removed, see Known Issues
  - [x] Beets config: path templates, match thresholds, plugins (chroma, embedart, fetchart, inline, musicbrainz, replaygain, scrub)
  - [x] Test rip: Grant Green - Feelin' the Spirit (Blue Note, 2005 reissue)
    - All 6 tracks AccurateRip-verified at confidence 17-18
    - Files landed at `/mnt/tank/music/library/FLAC/CDRips/Grant Green/2005 - Feelin' the Spirit/`
    - Naming pattern verified: `NN - Artist - Title.flac`
- [x] **Phase 1:** Manual rip runbook, confirmed against 3 live rips on 2026-07-26
  (Ry Cooder & Manuel Galbán, Reflection Eternal, John Coltrane). See
  `docs/phase1-runbook.md` v2 — all items resolved except Navidrome rescan trigger.
- [x] **Phase 2:** Python orchestrator (`ripper_orchestrator.py`). Wraps whipper + beets
  with logging, config, `--dry-run`, and error handling. Confirmed working end-to-end
  across 3 real discs, including automatic recovery/handling of Known Issues A-C below.
- [x] **Phase 3:** Hands-off automation. udev rule + systemd service triggers the
  orchestrator on disc insertion. Confirmed working end-to-end live 2026-07-27
  (Das EFX - Dead Serious): a real physical disc insertion fired the udev rule,
  which started `cdrip.service`, which ran the full pipeline (rip -> tag ->
  log/cue -> art -> Navidrome rescan -> cleanup) completely unattended. Three
  real bugs were found and fixed during this testing -- see Known Issues.
- [x] **Phase 4:** Remote notifications + decisions via a self-hosted Telegram bot
  (`notify.py`, `telegram_poller.py`). Milestone pushes at each pipeline stage
  (disc detected, rip verified, tagged, Navidrome rescanned, complete), plus
  tap-to-decide inline-keyboard buttons for the two `--non-interactive`
  decision points (whipper rip failure, ambiguous destination folder).
  Confirmed working end-to-end live 2026-07-27: real disc insertions
  triggered pushes, and a live button tap round-tripped through Telegram
  correctly. Two unrelated real bugs were found and fixed during this
  testing -- see Known Issues. (Originally designed around Home Assistant,
  then ntfy; switched to Telegram to keep home-automation and CD-ripping
  notifications on separate systems.)

## Known issues (carried into later phases)

- **RESOLVED (2026-07-27) — `cdrip.service` hardcoded the wrong repo path**
  (`/home/kevin/cdrip-pipeline` instead of the actual clone location,
  `/home/kevin/projects/cdrip-pipeline`), causing `Failed to load environment
  files` and `Failed to spawn 'start' task` on the very first udev-triggered
  run. Fixed by correcting `WorkingDirectory`/`EnvironmentFile`/`ExecStart` in
  both the installed unit and the repo's `systemd/cdrip.service`.
- **RESOLVED (2026-07-27) — `rip()` couldn't find the ripped album folder**
  after the first-ever rip in a given staging directory. It used to diff
  `os.listdir(staging_dir)` before/after the rip looking for a newly-created
  top-level entry, but whipper's wrapper folder (e.g. `album`) is reused
  across runs, not recreated -- so it's never actually "new" again after the
  first rip. Interactive runs papered over this because the fallback prompt
  lists every folder (not just new ones) and a human just picked the obvious
  one each time; `--non-interactive` had no one to do that, so it surfaced
  immediately. **Fix: walk the whole staging tree and use whichever folder
  actually contains `.flac` files with the newest mtime**, regardless of
  wrapper naming or reuse.
- **RESOLVED (2026-07-27) — `sudo`-prefixed commands fail instantly under
  systemd** (`beet import`, `beet fetchart`, the log/cue `cp`) with `sudo: a
  terminal is required to read the password`. Every interactive test that
  night had a real terminal for `sudo` to prompt on (or a cached credential);
  systemd has no TTY at all, so this was always going to break the first time
  Phase 3 ran genuinely unattended. **Fix: a narrowly-scoped passwordless-sudo
  rule** in `/etc/sudoers.d/cdrip-pipeline` for exactly `/usr/bin/beet` and
  `/usr/bin/cp` (not a blanket `NOPASSWD: ALL`) -- see
  `docs/phase3-runbook.md` for the exact setup and how to verify it with
  `sudo -n`, which mimics no-TTY sudo so a cached terminal credential can't
  give a false pass.

- **RESOLVED (2026-07-27) — Beets autotagger returned "no candidates" for every
  MusicBrainz lookup.** Root cause was two stacked config problems, not the suspected
  beets 2.8.0 / Python 3.14 / musicbrainzngs incompatibility: (1) the `musicbrainz`
  plugin was never added to the `plugins:` list — MusicBrainz support moved out of
  beets core into an opt-in plugin at some point, so there were zero registered
  metadata sources to query; (2) a bogus `search_ids: [mb_albumid, mb_trackid,
  mb_releasetrackid]` line under `import:` fed those literal field *names* to beets
  as real MBIDs, producing `Invalid MBID (mb_albumid)` errors and breaking ID-based
  lookup too. **Fix: add `musicbrainz` to `plugins:`, delete the `search_ids` line.**
  Confirmed live — re-importing Grant Green's *Feelin' the Spirit* now gets a strong
  (93.7%) automatic ID match against the correct release with zero manual
  intervention. "Use as-is" is no longer required for new imports; it remains a
  valid fallback only for discs genuinely absent from MusicBrainz (see the Dizzy
  Gillespie y Machito case, Phase 2 notes).
- **beets-extrafiles plugin crashes on `cli_exit`** with `AttributeError: module
  'beets.library' has no attribute 'DefaultTemplateFunctions'`. Confirmed live, exactly as
  suspected — the crash happens after the real audio import/move already succeeds, so it
  doesn't corrupt the rip, it just breaks the plugin's own post-import log/cue copy step.
  **Resolved: `extrafiles` removed from the beets config entirely.** The orchestrator
  copies `.log`/`.cue` manually instead (`copy_log_cue()`).
- **fetchart/embedart don't fire automatically after a "Use as-is" import** (new, found
  during Phase 2 live testing). Both plugins work fine when invoked directly — the
  orchestrator now runs `beet fetchart path:<destination-folder>` as its own explicit step
  after every import, which also triggers embedart's auto-embed hook.
- **NFS mount-check gotcha:** the mount check must target the actual systemd mountpoint
  (`/mnt/tank/music`), not `/mnt/tank/music/library` (a subfolder inside it) — the latter
  will never register as mounted via `os.path.ismount()`/`mountpoint`, even when the NFS
  mount is completely healthy. Also: after editing the automount entry in `/etc/fstab`,
  `sudo systemctl daemon-reload` is needed before the automount unit exists/works.
- **Permissions model:** beets must run as root for writes to land on the NAS (Maproot=apps on the NFS share remaps root → apps user, which owns the dataset). Confirmed `sudo beet -c ~/.config/beets/config.yaml ...` (explicit config path) correctly overlays the user's own config rather than falling back to `/root/.config/beets/`. Aligns with Phase 3's systemd service model.
- **RESOLVED (2026-07-27) — `beet import` failed with `no such option: -q` under
  `--non-interactive`.** The orchestrator was passing `-q` as a global flag before
  the `import` subcommand, but beets treats `-q`/`--quiet` as an `import`-specific
  option, not a top-level one. This silently broke every non-interactive import --
  surfaced only once beets was updated via a routine `pacman` upgrade and its CLI
  parsing got stricter about flag placement. **Fix: append `-q` after `import`,
  not before it.**
- **RESOLVED (2026-07-27) — udev rule didn't fire on mixed-mode/enhanced CDs.**
  The rule matched on `ENV{ID_FS_TYPE}==""` as its "this is a pure audio CD"
  signal, but mixed-mode discs (audio tracks plus a data session, common on some
  90s CDs with bonus content) report a real filesystem (`iso9660`/`udf`) for that
  data session, so the rule silently never matched -- `SYSTEMD_WANTS=cdrip.service`
  never got set, and the disc just sat in the drive with nothing happening.
  **Fix: match on `ENV{ID_CDROM_MEDIA_TRACK_COUNT_AUDIO}=="?*"` (has at least one
  audio track) instead of inferring it from the absence of a filesystem.**
- **OPEN — `cdrip.service` stays wedged in a "failed" state after any failure**,
  and won't start again on a subsequent disc insertion until
  `sudo systemctl reset-failed cdrip.service` is run manually. Discovered when a
  disc inserted after an earlier failed run produced no activity at all --
  `systemctl status` was just showing the stale prior failure. Not yet fixed
  structurally (e.g. having the udev rule or a wrapper auto-reset the failed
  state); for now, run `systemctl reset-failed` after any failed rip before
  expecting the next disc to auto-trigger.
- **RESOLVED — Navidrome rescan trigger.** Verified live via the Subsonic
  `startScan`/`getScanStatus` API. Enabled in `config.yaml`; credentials moved to
  environment variables (`NAVIDROME_USER`/`NAVIDROME_PASS` in a gitignored `.env`,
  never committed to the repo).

## Status

Phases 0-4 complete and verified live (most recently 2026-07-27: Phase 4 Telegram
notifications/decisions, plus a `beet import` flag-order bug and a udev rule that
missed mixed-mode CDs, both found and fixed live). One open gap: `cdrip.service`
needs a manual `systemctl reset-failed` after any failure before the next disc
will auto-trigger -- see Known Issues. Not yet started: CD tracker spreadsheet /
barcode intake.
