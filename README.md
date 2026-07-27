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
- **Trigger:** udev rule on `/dev/sr0` insertion -> systemd service -> orchestrator (not yet built — Phase 3)
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
- [ ] **Phase 4:** Polish. Notifications (ntfy / Home Assistant), TUI status, full documentation.

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
- **Navidrome rescan trigger — still open.** Not yet exercised on a live rip; disabled in
  orchestrator config pending verification of auto-scan interval vs. manual API trigger.

## Status

Phase 0, 1, and 2 complete and verified against 3 real live rips (2026-07-26). Next up:
Phase 3 (udev + systemd automation).
