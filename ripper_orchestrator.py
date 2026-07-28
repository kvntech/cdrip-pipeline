#!/usr/bin/env python3
"""
ripper_orchestrator.py — Phase 2 of cdrip-pipeline

Wraps whipper + beets into one command per docs/phase1-runbook.md:
stage -> rip -> import/tag -> copy log+cue -> (optional) Navidrome rescan
-> clean staging.

This directly implements the manual runbook, including its one remaining
documented workaround:
  - Known issue B: beets-extrafiles crashes on cli_exit, so .log/.cue files
    are copied by this script instead of relying on that plugin.

Known issue A (beets MusicBrainz autotagger returning zero candidates) was
root-caused and fixed 2026-07-27: the `musicbrainz` plugin was missing from
beets' plugins list, and a bogus `search_ids` line under `import:` in the
beets config was feeding literal field names to beets as if they were real
MBIDs. See README.md for details. Automatic MusicBrainz matching during
`beet import` now works without manual intervention.

Navidrome rescan (Subsonic API `startScan`/`getScanStatus`) was verified
working live 2026-07-27. Credentials are read from the NAVIDROME_USER /
NAVIDROME_PASS environment variables, never from config.yaml, since this
repo is public.

Run on arch-box, not on the sandboxed dev machine this was written in.

Usage:
    python3 ripper_orchestrator.py                 # full interactive run
    python3 ripper_orchestrator.py --dry-run        # print commands, do nothing
    python3 ripper_orchestrator.py --keep-staging   # skip cleanup step
    python3 ripper_orchestrator.py --skip-navidrome # skip rescan even if enabled in config
    python3 ripper_orchestrator.py --config other.yaml

Environment variables:
    NAVIDROME_USER, NAVIDROME_PASS   required if navidrome.enabled in config.yaml
"""

import argparse
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

import notify  # Phase 4: HA Companion App notifications + remote decisions

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")

try:
    import requests
except ImportError:
    requests = None  # only needed if navidrome.enabled


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for key in ("staging_dir", "log_dir", "beets_config", "nfs_mountpoint", "library_root"):
        if key in cfg:
            cfg[key] = os.path.expanduser(cfg[key])
    return cfg


def setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(log_dir, f"rip-{ts}.log")
    logger = logging.getLogger("orchestrator")
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Log file: {log_path}")
    return logger


def run(cmd, log, dry_run, **kwargs):
    """Run a command, or just print it in dry-run mode."""
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    log.info(f"$ {printable}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return subprocess.run(cmd, **kwargs)


# ---------------------------------------------------------------------------
# Step 0: pre-flight checks
# ---------------------------------------------------------------------------

def preflight_checks(cfg, log, dry_run) -> bool:
    log.info("== Pre-flight checks ==")
    ok = True

    # NFS mount — check the actual systemd mountpoint (nfs_mountpoint), NOT
    # library_root, which is just a subfolder inside that mount and will
    # never itself register as a mountpoint via os.path.ismount().
    mount_path = cfg.get("nfs_mountpoint", cfg["library_root"])
    if not dry_run:
        mounted = os.path.ismount(mount_path)
        if not mounted:
            # wake a lazy/idle-timed-out automount, then re-check
            try:
                os.listdir(mount_path)
            except OSError:
                pass
            mounted = os.path.ismount(mount_path)
        if not mounted:
            log.error(f"NFS not mounted at {mount_path}")
            ok = False
        else:
            log.info(f"NFS mounted at {mount_path}")
    else:
        log.info(f"[dry-run] would check mountpoint {mount_path}")

    # Drive present
    if not dry_run:
        if not os.path.exists(cfg["drive_device"]):
            log.error(f"Drive not found: {cfg['drive_device']}")
            ok = False
        else:
            log.info(f"Drive present: {cfg['drive_device']}")
    else:
        log.info(f"[dry-run] would check drive {cfg['drive_device']}")

    # whipper sees the drive
    result = run(["whipper", "drive", "list"], log, dry_run,
                 capture_output=True, text=True)
    if not dry_run and result.returncode != 0:
        log.error("`whipper drive list` failed:\n" + (result.stderr or ""))
        ok = False

    # beets sane (expects 7 plugins per README: chroma, embedart, fetchart,
    # inline, musicbrainz, replaygain, scrub)
    result = run(["beet", "version"], log, dry_run, capture_output=True, text=True)
    if not dry_run:
        if result.returncode != 0:
            log.error("`beet version` failed:\n" + (result.stderr or ""))
            ok = False
        else:
            log.info(result.stdout.strip())

    return ok


# ---------------------------------------------------------------------------
# Step 1-2: stage + rip
# ---------------------------------------------------------------------------

def stage(cfg, log, dry_run, non_interactive: bool) -> str:
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
            notify.notify(log, "cdrip: disc detected, starting rip.")
    else:
        input("Insert CD, wait for spin-up, then press Enter to continue... ")
    return staging


def _find_ripped_album_folder(staging_dir, log):
    """Find whipper's actual per-disc output folder: whichever directory
    under staging_dir directly contains .flac files with the newest mtime.

    Confirmed live 2026-07-27 (TLC - CrazySexyCool): the previous approach --
    diffing staging_dir's top-level entries before/after the rip, looking for
    a "new" one -- breaks the moment whipper's wrapper folder (e.g. "album")
    is reused across runs, since it's never actually new again after the
    first rip ever done in that staging dir. This doesn't care how many times
    that wrapper gets reused or what it's named; it also makes the old
    wrapper-descent logic unnecessary, since walking the tree already lands
    on the real leaf folder.
    """
    best_path = None
    best_mtime = -1.0
    for root, _dirs, files in os.walk(staging_dir):
        flacs = [f for f in files if f.endswith(".flac")]
        if not flacs:
            continue
        mtime = max(os.path.getmtime(os.path.join(root, f)) for f in flacs)
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = root
    if best_path:
        log.info(f"Found ripped album folder (newest .flac mtime): {best_path}")
    return best_path


def rip(cfg, log, dry_run, non_interactive: bool) -> str:
    """Run whipper and return the path to the album folder it created.

    See _find_ripped_album_folder() for how the folder is located -- this
    used to diff staging_dir's top-level contents before/after the rip, which
    broke once whipper's wrapper folder got reused across runs (confirmed
    live 2026-07-27).
    """
    log.info("== Rip (whipper) ==")
    staging = cfg["staging_dir"]

    extra_args = list(cfg.get("whipper_extra_args", []))
    cmd = ["whipper", "cd", "rip"] + extra_args
    result = run(cmd, log, dry_run, cwd=staging)

    if not dry_run and result.returncode != 0:
        # A disc that isn't in MusicBrainz at all makes whipper fail outright
        # rather than just returning a bad match -- confirmed live 2026-07-26
        # (Dizzy Gillespie y Machito, before a MusicBrainz release existed for
        # it). --unknown tells whipper to proceed without a MusicBrainz TOC
        # match.
        if "--unknown" in extra_args:
            log.error("whipper rip failed even with --unknown. Giving up.")
            notify.notify(log, "cdrip: whipper rip failed even with --unknown. Giving up.")
            sys.exit(1)
        if non_interactive:
            # Phase 4: try a remote decision (actionable push, wait for a
            # phone tap) before falling back to the old blind auto-retry.
            # NOT YET TESTED LIVE.
            decision = notify.ask(
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
            notify.notify(log, "cdrip: whipper rip failed again with --unknown. Giving up.")
            sys.exit(1)

    if dry_run:
        return os.path.join(staging, "<album-folder>")

    album_path = _find_ripped_album_folder(staging, log)
    if album_path is None:
        log.error(f"Could not find any folder containing .flac files under {staging}. "
                  "Leaving staging as-is for manual inspection.")
        notify.notify(log, "cdrip: could not find the ripped album folder after "
                          "whipper finished. Check arch-box.")
        sys.exit(1)

    log.info(f"Rip staged at: {album_path}")
    notify.notify(log, f"cdrip: rip verified, now tagging: {os.path.basename(album_path)}")
    return album_path


# ---------------------------------------------------------------------------
# Step 3: import + tag with beets
# ---------------------------------------------------------------------------

def import_beets(cfg, log, dry_run, album_path: str, non_interactive: bool):
    log.info("== Import + tag (beets) ==")
    if cfg.get("known_issue_a_reminder", False):
        log.info(
            "Reminder (known issue A, resolved 2026-07-27): if beets ever shows "
            "'Evaluating 0 candidates' again, check that the `musicbrainz` plugin "
            "is in beets' plugins list and that there's no stray `search_ids` line "
            "under `import:` in its config -- both caused this before. 'Use as-is' "
            "is now a fallback for discs genuinely absent from MusicBrainz, not the "
            "expected path."
        )

    beets_config = cfg["beets_config"]
    cmd = []
    if cfg.get("beets_use_sudo", True):
        cmd.append("sudo")
    cmd += ["beet", "-c", beets_config]
    cmd.append("import")
    if non_interactive:
        # Quiet mode never prompts: a strong match applies automatically,
        # anything weaker falls back to whatever import.quiet_fallback says
        # in the beets config. NOT YET VERIFIED LIVE against a real
        # ambiguous-match disc -- see docs/phase3-runbook.md. Set
        # `quiet_fallback: asis` there so an unresolved match still keeps
        # whipper's own correct tags instead of being skipped.
        cmd.append("-q")
    cmd.append(album_path)

    if non_interactive:
        log.info("Running import in quiet/non-interactive mode (-q).")
    else:
        log.info(
            "Running import interactively so you can resolve the MusicBrainz "
            "candidate prompt yourself, if one comes up."
        )
    if dry_run:
        run(cmd, log, dry_run)
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        # As of the 2026-07-27 fix, `extrafiles` is no longer in the plugins
        # list (known issue B), so a non-zero exit here is NOT expected to be
        # that old harmless cli_exit crash anymore -- treat it as a real
        # problem.
        log.warning("beet import exited non-zero. Check the output above.")
        if non_interactive:
            log.error("Non-interactive mode: stopping rather than guessing whether "
                      "to continue.")
            notify.notify(log, "cdrip: beet import failed (non-zero exit). Stopped -- "
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
        notify.notify(log, f"cdrip: tagged, fetching art next: {os.path.basename(album_path)}")


# ---------------------------------------------------------------------------
# Step 4: copy log + cue (extrafiles workaround)
# ---------------------------------------------------------------------------

def find_dest_folder(cfg, log, dry_run, non_interactive: bool) -> str:
    """Suggest the destination album folder by looking for whatever changed
    most recently under library_root/cdrips_subdir. Interactively, ask the
    user to confirm/override; non-interactively, only proceed on an
    unambiguous single match."""
    library_dir = os.path.join(cfg["library_root"], cfg["cdrips_subdir"])
    if dry_run:
        return os.path.join(library_dir, "<Artist>", "<Year - Album>")

    window = cfg.get("dest_autodetect_window_minutes", 15) * 60
    now = time.time()
    candidates = []
    if os.path.isdir(library_dir):
        for artist_dir in Path(library_dir).iterdir():
            if not artist_dir.is_dir():
                continue
            for album_dir in artist_dir.iterdir():
                if not album_dir.is_dir():
                    continue
                mtime = album_dir.stat().st_mtime
                if now - mtime <= window:
                    candidates.append(str(album_dir))

    if len(candidates) == 1:
        suggestion = candidates[0]
        if non_interactive:
            log.info(f"Destination folder (auto-confirmed): {suggestion}")
            return suggestion
        confirm = input(f"Destination folder: {suggestion} — correct? [Y/n] ").strip().lower()
        if confirm in ("", "y", "yes"):
            return suggestion
    elif candidates:
        if non_interactive:
            # Phase 4: with exactly 2 candidates we can offer both as
            # tappable buttons (iOS actionable notifications comfortably fit
            # 2-3). With more than that, fall through to the old
            # fail-loudly behavior rather than truncating the choice.
            # NOT YET TESTED LIVE.
            if len(candidates) == 2:
                decision = notify.ask(
                    log, cfg,
                    "cdrip: 2 possible destination folders found, which is correct?\n"
                    f"1: {candidates[0]}\n2: {candidates[1]}",
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
            notify.notify(log, f"cdrip: {len(candidates)} possible destination folders, "
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
        notify.notify(log, "cdrip: no destination folder candidates found after import. "
                          "Check arch-box.")
        sys.exit(1)

    return input("Enter the destination album folder path: ").strip()


def copy_log_cue(cfg, log, dry_run, album_path: str, dest_folder: str):
    log.info("== Copy .log/.cue (extrafiles workaround) ==")
    log_cue_files = []
    if os.path.isdir(album_path):
        for f in os.listdir(album_path):
            if f.endswith(".log") or f.endswith(".cue"):
                log_cue_files.append(os.path.join(album_path, f))

    if not log_cue_files and not dry_run:
        log.warning(f"No .log/.cue files found in {album_path} — nothing to copy.")
        return

    cmd_parts = ["sudo", "cp"] if cfg.get("beets_use_sudo", True) else ["cp"]
    cmd = cmd_parts + log_cue_files + [dest_folder + "/"]
    run(cmd, log, dry_run)


def fetch_art(cfg, log, dry_run, dest_folder: str):
    """Explicitly fetch + embed album art.

    Confirmed live 2026-07-26: fetchart/embedart don't fire automatically
    during the "Use as-is" import path (known issue A's fallback), even
    though both plugins work fine when invoked directly. Rather than rely on
    beets doing this as a side effect of import, run it explicitly here --
    same reasoning as the log/cue copy step. Targets the exact album via a
    beets path: query (confirmed working) so it can't grab art for the wrong
    album.
    """
    log.info("== Fetch + embed album art ==")
    beets_config = cfg["beets_config"]
    cmd = []
    if cfg.get("beets_use_sudo", True):
        cmd.append("sudo")
    cmd += ["beet", "-c", beets_config, "fetchart", f"path:{dest_folder}"]
    result = run(cmd, log, dry_run)
    if not dry_run and result.returncode != 0:
        log.warning("beet fetchart exited non-zero — album art may not have been fetched.")


# ---------------------------------------------------------------------------
# Step 5: Navidrome rescan (Subsonic API)
# ---------------------------------------------------------------------------

def trigger_navidrome_rescan(cfg, log, dry_run, skip_flag: bool):
    nav = cfg.get("navidrome", {})
    if skip_flag or not nav.get("enabled", False):
        log.info("Navidrome rescan skipped (disabled in config or --skip-navidrome).")
        return

    if requests is None:
        log.error("`requests` not installed — cannot call Navidrome API. pip install requests")
        return

    # Credentials come from the environment, never from config.yaml -- this
    # repo is public and config.yaml is tracked in git. Verified working live
    # 2026-07-27 via the Subsonic startScan/getScanStatus endpoints (scan
    # completed cleanly: count 1144, folderCount 83).
    username = os.environ.get("NAVIDROME_USER")
    password = os.environ.get("NAVIDROME_PASS")
    if not username or not password:
        log.error(
            "Navidrome rescan enabled but NAVIDROME_USER/NAVIDROME_PASS are not "
            "set in the environment. Skipping rescan. (Do not put credentials in "
            "config.yaml -- this repo is public.)"
        )
        return

    # Subsonic API auth: token = md5(password + salt)
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    token = hashlib.md5((password + salt).encode()).hexdigest()
    params = {
        "u": username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "cdrip-pipeline",
        "f": "json",
    }
    url = nav["url"].rstrip("/") + "/rest/startScan"
    log.info(f"== Navidrome rescan == GET {url}")
    if dry_run:
        return
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        log.info(f"Navidrome rescan triggered: {resp.json()}")
        notify.notify(log, "cdrip: Navidrome rescan triggered, wrapping up.")
    except requests.RequestException as e:
        log.error(f"Navidrome rescan failed: {e}")
        notify.notify(log, f"cdrip: Navidrome rescan failed: {e}")


# ---------------------------------------------------------------------------
# Step 6: validation + cleanup
# ---------------------------------------------------------------------------

def validate(cfg, log, dry_run, dest_folder: str):
    log.info("== Validation checklist ==")
    if dry_run:
        log.info("[dry-run] skipping validation checks")
        return True

    ok = True
    flacs = [f for f in os.listdir(dest_folder) if f.endswith(".flac")] \
        if os.path.isdir(dest_folder) else []
    if flacs:
        log.info(f"[x] {len(flacs)} FLAC file(s) present in {dest_folder}")
    else:
        log.error(f"[ ] No FLAC files found in {dest_folder}")
        ok = False

    has_log = any(f.endswith(".log") for f in os.listdir(dest_folder)) if os.path.isdir(dest_folder) else False
    has_cue = any(f.endswith(".cue") for f in os.listdir(dest_folder)) if os.path.isdir(dest_folder) else False
    log.info(f"[{'x' if has_log else ' '}] .log present")
    log.info(f"[{'x' if has_cue else ' '}] .cue present")
    ok = ok and has_log and has_cue

    has_art = any(f.lower() in ("cover.jpg", "cover.png", "folder.jpg") or
                  f.lower().endswith((".jpg", ".jpeg", ".png"))
                  for f in os.listdir(dest_folder)) if os.path.isdir(dest_folder) else False
    log.info(f"[{'x' if has_art else ' '}] cover art file present")
    if not has_art:
        log.warning("No cover art file found — check `beet fetchart` output above.")

    log.info("Remaining checklist items to confirm by ear/eye:")
    log.info("  - Tags populated (album, albumartist, year, track, title)")
    log.info("  - Album visible and playable in Navidrome")

    status = "complete" if ok else "finished with issues -- check logs on arch-box"
    notify.notify(log, f"cdrip: {status}: {os.path.basename(dest_folder)}")

    return ok


def clean_staging(cfg, log, dry_run, album_path: str, keep_staging: bool, non_interactive: bool):
    log.info("== Clean staging ==")
    if keep_staging:
        log.info("Skipping cleanup (--keep-staging).")
        return
    if cfg.get("prompt_before_cleanup", True) and not dry_run:
        if non_interactive:
            log.info("Non-interactive mode: skipping cleanup confirmation, deleting staging.")
        else:
            confirm = input(f"Delete staging folder {album_path}? [y/N] ").strip().lower()
            if confirm != "y":
                log.info("Cleanup skipped by user.")
                return
    if dry_run:
        log.info(f"[dry-run] would rm -rf {album_path}")
        return
    shutil.rmtree(album_path)
    log.info(f"Removed {album_path}")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="cdrip-pipeline Phase 2 orchestrator")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="print commands, execute nothing")
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument("--skip-navidrome", action="store_true")
    parser.add_argument("--non-interactive", action="store_true",
                         help="Phase 3: no prompts, no waiting on a TTY (for udev/systemd). "
                              "Fails loudly instead of blocking wherever a human judgment "
                              "call would normally be needed.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg["log_dir"] if not args.dry_run else "/tmp")

    if not preflight_checks(cfg, log, args.dry_run):
        log.error("Pre-flight checks failed. Fix the issues above before ripping.")
        if not args.dry_run:
            sys.exit(1)

    stage(cfg, log, args.dry_run, args.non_interactive)
    album_path = rip(cfg, log, args.dry_run, args.non_interactive)
    import_beets(cfg, log, args.dry_run, album_path, args.non_interactive)
    dest_folder = find_dest_folder(cfg, log, args.dry_run, args.non_interactive)
    copy_log_cue(cfg, log, args.dry_run, album_path, dest_folder)
    fetch_art(cfg, log, args.dry_run, dest_folder)
    trigger_navidrome_rescan(cfg, log, args.dry_run, args.skip_navidrome)
    validate(cfg, log, args.dry_run, dest_folder)
    clean_staging(cfg, log, args.dry_run, album_path, args.keep_staging, args.non_interactive)

    log.info("Done.")


if __name__ == "__main__":
    main()
