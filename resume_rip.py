#!/usr/bin/env python3
"""Reusable recovery tool: resume the pipeline for a disc that already got
ripped (and is sitting somewhere under staging/) but didn't make it through
import_beets() onward -- e.g. because of the sudo-requires-TTY bug hit
2026-07-27, or any other reason a run got interrupted after a successful
rip. Reuses the orchestrator's own tested functions rather than doing
anything by hand differently.

Runs interactively (not --non-interactive) since a human is present to
resolve it -- if beets prompts for an ambiguous match, pick based on your
physical CD's barcode/region rather than guessing.

Usage (from repo root, with .env sourced):
    set -a; source .env; set +a
    python3 resume_rip.py "/home/kevin/cd-rips/staging/album/Das EFX - Dead Serious"

If you're not sure of the exact staged path, this will help find it:
    find ~/cd-rips/staging -name "*.flac" -printf "%h\\n" | sort -u
"""
import sys

import ripper_orchestrator as orch

if len(sys.argv) != 2:
    sys.exit(f"Usage: {sys.argv[0]} <path to already-ripped album folder>")

ALBUM_PATH = sys.argv[1]

cfg = orch.load_config("config.yaml")
log = orch.setup_logging(cfg["log_dir"])

log.info(f"Resuming pipeline for stranded rip at: {ALBUM_PATH}")

orch.import_beets(cfg, log, False, ALBUM_PATH, non_interactive=False)
dest_folder = orch.find_dest_folder(cfg, log, False, non_interactive=False)
orch.copy_log_cue(cfg, log, False, ALBUM_PATH, dest_folder)
orch.fetch_art(cfg, log, False, dest_folder)
orch.trigger_navidrome_rescan(cfg, log, False, skip_flag=False)
orch.validate(cfg, log, False, dest_folder)
orch.clean_staging(cfg, log, False, ALBUM_PATH, keep_staging=False, non_interactive=False)

log.info("Recovery complete.")
