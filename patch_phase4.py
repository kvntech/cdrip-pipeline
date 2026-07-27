#!/usr/bin/env python3
"""Wire Home Assistant remote decisions + progress notifications into
ripper_orchestrator.py (Phase 4). Requires ha_notify.py to be present in the
same directory. Run this AFTER patch_ripfix.py has already been applied.

Adds:
  - One-way progress pushes at each pipeline stage (disc detected, rip
    verified, tagged, Navidrome rescanned, final complete/issues summary).
  - Remote decision (actionable push + wait for phone tap) at the two
    non-interactive decision points that previously just auto-retried or
    failed immediately: whipper rip failure (retry with --unknown?) and an
    ambiguous destination folder (pick between exactly 2 candidates).
  - Falls back to the pre-Phase-4 non-interactive default behavior if HA
    isn't configured, sending failed, or nothing came back in time -- this
    never makes the pipeline MORE likely to hang than it already was.

NOT YET TESTED LIVE -- see docs/phase4-runbook.md.
"""
import pathlib
import sys

ROOT = pathlib.Path(".")


def patch(path, replacements):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    for old, new in replacements:
        if old not in text:
            print(f"!! Pattern not found in {path}, skipping this replacement:")
            print(old[:150].replace("\n", " ") + " ...")
            sys.exit(1)
        text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print(f"patched {path}")


replacements = [
    (
        '''import argparse
import hashlib
import logging
import os
import random
import shutil
import string
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")''',
        '''import argparse
import hashlib
import logging
import os
import random
import shutil
import string
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import ha_notify  # Phase 4: HA Companion App notifications + remote decisions

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")''',
    ),
    (
        '''def stage(cfg, log, dry_run, non_interactive: bool) -> str:
    log.info("== Stage ==")
    staging = cfg["staging_dir"]
    if dry_run:
        log.info(f"[dry-run] would mkdir -p {staging}")
    else:
        os.makedirs(staging, exist_ok=True)
    if non_interactive:
        # Under udev/systemd (Phase 3) the disc-insertion event IS the
        # trigger -- there's no TTY to wait on, and no need to wait for
        # spin-up since udev only fires after the kernel already saw the
        # media change.
        log.info("Non-interactive mode: skipping 'insert CD' prompt.")
    else:
        input("Insert CD, wait for spin-up, then press Enter to continue... ")
    return staging''',
        '''def stage(cfg, log, dry_run, non_interactive: bool) -> str:
    log.info("== Stage ==")
    staging = cfg["staging_dir"]
    if dry_run:
        log.info(f"[dry-run] would mkdir -p {staging}")
    else:
        os.makedirs(staging, exist_ok=True)
    if non_interactive:
        # Under udev/systemd (Phase 3) the disc-insertion event IS the
        # trigger -- there's no TTY to wait on, and no need to wait for
        # spin-up since udev only fires after the kernel already saw the
        # media change.
        log.info("Non-interactive mode: skipping 'insert CD' prompt.")
        if not dry_run:
            ha_notify.notify(log, "cdrip: disc detected, starting rip.")
    else:
        input("Insert CD, wait for spin-up, then press Enter to continue... ")
    return staging''',
    ),
    (
        '''    if not dry_run and result.returncode != 0:
        # A disc that isn't in MusicBrainz at all makes whipper fail outright
        # rather than just returning a bad match -- confirmed live 2026-07-26
        # (Dizzy Gillespie y Machito, before a MusicBrainz release existed for
        # it). --unknown tells whipper to proceed without a MusicBrainz TOC
        # match.
        if "--unknown" in extra_args:
            log.error("whipper rip failed even with --unknown. Giving up.")
            sys.exit(1)
        if non_interactive:
            retry = True
            log.warning("whipper rip failed. Non-interactive mode: auto-retrying "
                        "with --unknown once.")
        else:
            retry = input(
                "whipper rip failed -- possibly a disc not in MusicBrainz. "
                "Retry with --unknown? [y/N] "
            ).strip().lower() == "y"
        if not retry:
            log.error("whipper rip failed. Not retrying.")
            sys.exit(1)
        cmd = ["whipper", "cd", "rip", "--unknown"] + extra_args
        result = run(cmd, log, dry_run, cwd=staging)
        if result.returncode != 0:
            log.error("whipper rip failed again with --unknown. Giving up.")
            sys.exit(1)

    if dry_run:
        return os.path.join(staging, "<album-folder>")

    album_path = _find_ripped_album_folder(staging, log)
    if album_path is None:
        log.error(f"Could not find any folder containing .flac files under {staging}. "
                  "Leaving staging as-is for manual inspection.")
        sys.exit(1)

    log.info(f"Rip staged at: {album_path}")
    return album_path''',
        '''    if not dry_run and result.returncode != 0:
        # A disc that isn't in MusicBrainz at all makes whipper fail outright
        # rather than just returning a bad match -- confirmed live 2026-07-26
        # (Dizzy Gillespie y Machito, before a MusicBrainz release existed for
        # it). --unknown tells whipper to proceed without a MusicBrainz TOC
        # match.
        if "--unknown" in extra_args:
            log.error("whipper rip failed even with --unknown. Giving up.")
            ha_notify.notify(log, "cdrip: whipper rip failed even with --unknown. Giving up.")
            sys.exit(1)
        if non_interactive:
            # Phase 4: try a remote decision (actionable push, wait for a
            # phone tap) before falling back to the old blind auto-retry.
            # NOT YET TESTED LIVE.
            decision = ha_notify.ask(
                log, cfg,
                "cdrip: whipper rip failed (disc maybe not in MusicBrainz). "
                "Retry with --unknown?",
                [("RETRY", "Retry with --unknown"), ("GIVE_UP", "Give up")],
            )
            if decision == "RETRY":
                retry = True
            elif decision == "GIVE_UP":
                retry = False
            else:
                retry = True
                log.warning("No remote decision available (HA not configured, send "
                            "failed, or no response in time). Non-interactive default: "
                            "auto-retrying with --unknown once.")
        else:
            retry = input(
                "whipper rip failed -- possibly a disc not in MusicBrainz. "
                "Retry with --unknown? [y/N] "
            ).strip().lower() == "y"
        if not retry:
            log.error("whipper rip failed. Not retrying.")
            sys.exit(1)
        cmd = ["whipper", "cd", "rip", "--unknown"] + extra_args
        result = run(cmd, log, dry_run, cwd=staging)
        if result.returncode != 0:
            log.error("whipper rip failed again with --unknown. Giving up.")
            ha_notify.notify(log, "cdrip: whipper rip failed again with --unknown. Giving up.")
            sys.exit(1)

    if dry_run:
        return os.path.join(staging, "<album-folder>")

    album_path = _find_ripped_album_folder(staging, log)
    if album_path is None:
        log.error(f"Could not find any folder containing .flac files under {staging}. "
                  "Leaving staging as-is for manual inspection.")
        ha_notify.notify(log, "cdrip: could not find the ripped album folder after "
                          "whipper finished. Check arch-box.")
        sys.exit(1)

    log.info(f"Rip staged at: {album_path}")
    ha_notify.notify(log, f"cdrip: rip verified, now tagging: {os.path.basename(album_path)}")
    return album_path''',
    ),
    (
        '''    elif candidates:
        if non_interactive:
            log.error(f"Non-interactive mode: {len(candidates)} candidate destination "
                      f"folders found, can't disambiguate: {candidates}")
            sys.exit(1)
        print("Multiple recently-modified album folders found:")
        for i, c in enumerate(candidates, 1):
            print(f"  [{i}] {c}")
        choice = input("Pick one, or 0 to type a path manually: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
    elif non_interactive:
        log.error("Non-interactive mode: no destination folder candidates found.")
        sys.exit(1)''',
        '''    elif candidates:
        if non_interactive:
            # Phase 4: with exactly 2 candidates we can offer both as
            # tappable buttons (iOS actionable notifications comfortably fit
            # 2-3). With more than that, fall through to the old
            # fail-loudly behavior rather than truncating the choice.
            # NOT YET TESTED LIVE.
            if len(candidates) == 2:
                decision = ha_notify.ask(
                    log, cfg,
                    "cdrip: 2 possible destination folders found, which is correct?\\n"
                    f"1: {candidates[0]}\\n2: {candidates[1]}",
                    [("FOLDER_1", os.path.basename(candidates[0])),
                     ("FOLDER_2", os.path.basename(candidates[1]))],
                )
                if decision == "FOLDER_1":
                    return candidates[0]
                if decision == "FOLDER_2":
                    return candidates[1]
                log.warning("No remote decision available for destination folder choice.")
            log.error(f"Non-interactive mode: {len(candidates)} candidate destination "
                      f"folders found, can't disambiguate: {candidates}")
            ha_notify.notify(log, f"cdrip: {len(candidates)} possible destination folders, "
                              "can't disambiguate. Check arch-box.")
            sys.exit(1)
        print("Multiple recently-modified album folders found:")
        for i, c in enumerate(candidates, 1):
            print(f"  [{i}] {c}")
        choice = input("Pick one, or 0 to type a path manually: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
    elif non_interactive:
        log.error("Non-interactive mode: no destination folder candidates found.")
        ha_notify.notify(log, "cdrip: no destination folder candidates found after import. "
                          "Check arch-box.")
        sys.exit(1)''',
    ),
    (
        '''    if result.returncode != 0:
        # As of the 2026-07-27 fix, `extrafiles` is no longer in the plugins
        # list (known issue B), so a non-zero exit here is NOT expected to be
        # that old harmless cli_exit crash anymore -- treat it as a real
        # problem.
        log.warning("beet import exited non-zero. Check the output above.")
        if non_interactive:
            log.error("Non-interactive mode: stopping rather than guessing whether "
                      "to continue.")
            sys.exit(1)
        confirm = input(
            "Continue the pipeline anyway (destination lookup + log/cue copy + "
            "validation)? [y/N] "
        ).strip().lower()
        if confirm != "y":
            log.error("Stopping at your request.")
            sys.exit(1)''',
        '''    if result.returncode != 0:
        # As of the 2026-07-27 fix, `extrafiles` is no longer in the plugins
        # list (known issue B), so a non-zero exit here is NOT expected to be
        # that old harmless cli_exit crash anymore -- treat it as a real
        # problem.
        log.warning("beet import exited non-zero. Check the output above.")
        if non_interactive:
            log.error("Non-interactive mode: stopping rather than guessing whether "
                      "to continue.")
            ha_notify.notify(log, "cdrip: beet import failed (non-zero exit). Stopped -- "
                              "check the log on arch-box.")
            sys.exit(1)
        confirm = input(
            "Continue the pipeline anyway (destination lookup + log/cue copy + "
            "validation)? [y/N] "
        ).strip().lower()
        if confirm != "y":
            log.error("Stopping at your request.")
            sys.exit(1)

    if not dry_run:
        ha_notify.notify(log, f"cdrip: tagged, fetching art next: {os.path.basename(album_path)}")''',
    ),
    (
        '''    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        log.info(f"Navidrome rescan triggered: {resp.json()}")
    except requests.RequestException as e:
        log.error(f"Navidrome rescan failed: {e}")''',
        '''    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        log.info(f"Navidrome rescan triggered: {resp.json()}")
        ha_notify.notify(log, "cdrip: Navidrome rescan triggered, wrapping up.")
    except requests.RequestException as e:
        log.error(f"Navidrome rescan failed: {e}")
        ha_notify.notify(log, f"cdrip: Navidrome rescan failed: {e}")''',
    ),
    (
        '''    log.info("Remaining checklist items to confirm by ear/eye:")
    log.info("  - Tags populated (album, albumartist, year, track, title)")
    log.info("  - Album visible and playable in Navidrome")
    return ok''',
        '''    log.info("Remaining checklist items to confirm by ear/eye:")
    log.info("  - Tags populated (album, albumartist, year, track, title)")
    log.info("  - Album visible and playable in Navidrome")

    status = "complete" if ok else "finished with issues -- check logs on arch-box"
    ha_notify.notify(log, f"cdrip: {status}: {os.path.basename(dest_folder)}")

    return ok''',
    ),
]

patch("ripper_orchestrator.py", replacements)
print("done")
