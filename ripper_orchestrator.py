#!/usr/bin/env python3
"""
ripper_orchestrator.py — Phase 2 of cdrip-pipeline

Wraps whipper + beets into one command per docs/phase1-runbook.md:
stage -> rip -> import/tag -> copy log+cue -> (optional) Navidrome rescan
-> clean staging.

This directly implements the manual runbook, including its two documented
workarounds:
  - Known issue A: beets MusicBrainz autotagger returns zero candidates.
    Workaround happens live inside beets' own interactive prompt (choose
    "Enter Id" and paste the MB release URL/ID, or "Use as-is").
  - Known issue B: beets-extrafiles crashes on cli_exit, so .log/.cue files
    are copied by this script instead of relying on that plugin.

Run on arch-box, not on the sandboxed dev machine this was written in.

Usage:
    python3 ripper_orchestrator.py                 # full interactive run
    python3 ripper_orchestrator.py --dry-run        # print commands, do nothing
    python3 ripper_orchestrator.py --keep-staging   # skip cleanup step
    python3 ripper_orchestrator.py --skip-navidrome # skip rescan even if enabled in config
    python3 ripper_orchestrator.py --config other.yaml
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

    # beets sane (expects 7 plugins per README: chroma, embedart, extrafiles,
    # fetchart, inline, replaygain, scrub)
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

def stage(cfg, log, dry_run) -> str:
    log.info("== Stage ==")
    staging = cfg["staging_dir"]
    if dry_run:
        log.info(f"[dry-run] would mkdir -p {staging}")
    else:
        os.makedirs(staging, exist_ok=True)
    input("Insert CD, wait for spin-up, then press Enter to continue... ")
    return staging


def rip(cfg, log, dry_run) -> str:
    """Run whipper and return the path to the album folder it created.

    Folder-name pattern isn't finalized in the runbook yet
    ([VERIFY ON LIVE RIP]), so instead of guessing the pattern this diffs the
    staging directory's contents before/after the rip to find whatever
    whipper actually created.
    """
    log.info("== Rip (whipper) ==")
    staging = cfg["staging_dir"]
    before = set(os.listdir(staging)) if os.path.isdir(staging) else set()

    cmd = ["whipper", "cd", "rip"] + cfg.get("whipper_extra_args", [])
    result = run(cmd, log, dry_run, cwd=staging)
    if not dry_run and result.returncode != 0:
        log.error("whipper rip failed")
        sys.exit(1)

    if dry_run:
        return os.path.join(staging, "<album-folder>")

    after = set(os.listdir(staging))
    new_dirs = [d for d in (after - before)
                if os.path.isdir(os.path.join(staging, d))]
    if len(new_dirs) != 1:
        log.warning(f"Expected exactly 1 new folder in staging, found {new_dirs}. "
                    "Pick manually.")
        for i, d in enumerate(sorted(after), 1):
            print(f"  [{i}] {d}")
        idx = int(input("Which folder is this rip? Enter number: ")) - 1
        chosen = sorted(after)[idx]
    else:
        chosen = new_dirs[0]

    album_path = os.path.join(staging, chosen)

    # Confirmed live 2026-07-26: whipper wraps its real per-disc output in a
    # fixed intermediate folder (observed literal name: "album") before the
    # actual "Artist - Title" folder. Descend through any chain of
    # single-subdirectory wrappers so album_path ends up at the folder that
    # actually contains the FLACs/.log/.cue, not the wrapper around it.
    while os.path.isdir(album_path):
        entries = os.listdir(album_path)
        if len(entries) == 1 and os.path.isdir(os.path.join(album_path, entries[0])):
            album_path = os.path.join(album_path, entries[0])
        else:
            break

    log.info(f"Rip staged at: {album_path}")
    return album_path


# ---------------------------------------------------------------------------
# Step 3: import + tag with beets
# ---------------------------------------------------------------------------

def import_beets(cfg, log, dry_run, album_path: str):
    log.info("== Import + tag (beets) ==")
    if cfg.get("known_issue_a_reminder", True):
        log.info(
            "Reminder (known issue A): if beets shows 'Evaluating 0 candidates' "
            "despite valid MusicBrainz tags from whipper, choose 'Enter Id' at "
            "the prompt and paste the release URL/ID, or 'Use as-is' to accept "
            "whipper's tags directly."
        )

    beets_config = cfg["beets_config"]
    cmd = []
    if cfg.get("beets_use_sudo", True):
        cmd.append("sudo")
    cmd += ["beet", "-c", beets_config, "import", album_path]

    log.info(
        "Running import interactively so you can resolve the MusicBrainz "
        "candidate prompt yourself."
    )
    if dry_run:
        run(cmd, log, dry_run)
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        # Confirmed live 2026-07-26: beets-extrafiles crashes with
        # AttributeError: module 'beets.library' has no attribute
        # 'DefaultTemplateFunctions' in its cli_exit hook (known issue B).
        # That hook fires AFTER the real import/move already succeeded, so a
        # non-zero exit here does NOT necessarily mean the audio import
        # failed -- it may just be this known-broken plugin dying in its own
        # post-import cleanup step. Don't kill the whole run on that; let the
        # operator check and decide.
        log.warning(
            "beet import exited non-zero. If the traceback above is from "
            "extrafiles' cli_exit hook (AttributeError: ...'DefaultTemplateFunctions'), "
            "the actual audio import likely still succeeded -- that plugin just "
            "crashes doing the .log/.cue copy this script already handles separately. "
            "Recommended permanent fix: remove 'extrafiles' from the plugins list in "
            "your beets config, since it's redundant with this script's copy_log_cue step."
        )
        confirm = input(
            "Continue the pipeline anyway (destination lookup + log/cue copy + "
            "validation)? [y/N] "
        ).strip().lower()
        if confirm != "y":
            log.error("Stopping at your request.")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Step 4: copy log + cue (extrafiles workaround)
# ---------------------------------------------------------------------------

def find_dest_folder(cfg, log, dry_run) -> str:
    """Suggest the destination album folder by looking for whatever changed
    most recently under library_root/cdrips_subdir, then let the user
    confirm or override."""
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
        confirm = input(f"Destination folder: {suggestion} — correct? [Y/n] ").strip().lower()
        if confirm in ("", "y", "yes"):
            return suggestion
    elif candidates:
        print("Multiple recently-modified album folders found:")
        for i, c in enumerate(candidates, 1):
            print(f"  [{i}] {c}")
        choice = input("Pick one, or 0 to type a path manually: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]

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

    # Subsonic API auth: token = md5(password + salt)
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    token = hashlib.md5((nav["password"] + salt).encode()).hexdigest()
    params = {
        "u": nav["username"],
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "cdrip-pipeline",
        "f": "json",
    }
    url = nav["url"].rstrip("/") + "/rest/startScan.view"
    log.info(f"== Navidrome rescan == GET {url}")
    if dry_run:
        return
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        log.info(f"Navidrome rescan triggered: {resp.json()}")
    except requests.RequestException as e:
        log.error(f"Navidrome rescan failed: {e}")


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
    return ok


def clean_staging(cfg, log, dry_run, album_path: str, keep_staging: bool):
    log.info("== Clean staging ==")
    if keep_staging:
        log.info("Skipping cleanup (--keep-staging).")
        return
    if cfg.get("prompt_before_cleanup", True) and not dry_run:
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
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg["log_dir"] if not args.dry_run else "/tmp")

    if not preflight_checks(cfg, log, args.dry_run):
        log.error("Pre-flight checks failed. Fix the issues above before ripping.")
        if not args.dry_run:
            sys.exit(1)

    stage(cfg, log, args.dry_run)
    album_path = rip(cfg, log, args.dry_run)
    import_beets(cfg, log, args.dry_run, album_path)
    dest_folder = find_dest_folder(cfg, log, args.dry_run)
    copy_log_cue(cfg, log, args.dry_run, album_path, dest_folder)
    fetch_art(cfg, log, args.dry_run, dest_folder)
    trigger_navidrome_rescan(cfg, log, args.dry_run, args.skip_navidrome)
    validate(cfg, log, args.dry_run, dest_folder)
    clean_staging(cfg, log, args.dry_run, album_path, args.keep_staging)

    log.info("Done.")


if __name__ == "__main__":
    main()
