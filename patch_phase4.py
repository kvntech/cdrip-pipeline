#!/usr/bin/env python3
"""
Phase 4 patch: wires notify.py's notifications + remote decisions into
ripper_orchestrator.py. Idempotent -- safe to run on a clean checkout;
does nothing if already applied.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).parent / "ripper_orchestrator.py"


def apply(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count == 0:
        print(f"FAILED to find anchor for: {label}")
        sys.exit(1)
    if count > 1:
        print(f"Anchor for {label!r} is not unique ({count} matches) -- refusing to guess.")
        sys.exit(1)
    return src.replace(old, new, 1)


def main():
    src = TARGET.read_text()

    if "import notify" in src:
        print("Already patched (import notify found). Nothing to do.")
        return

    src = apply(src,
        "from datetime import datetime\nfrom pathlib import Path\n\ntry:\n    import yaml",
        "from datetime import datetime\nfrom pathlib import Path\n\n"
        "import notify  # Phase 4: Telegram bot notifications + remote decisions\n\n"
        "try:\n    import yaml",
        "import notify")

    src = apply(src,
        '        log.info("Non-interactive mode: skipping \'insert CD\' prompt.")\n'
        "    else:\n"
        '        input("Insert CD, wait for spin-up, then press Enter to continue... ")',
        '        log.info("Non-interactive mode: skipping \'insert CD\' prompt.")\n'
        "        if not dry_run:\n"
        '            notify.notify(log, "cdrip: disc detected, starting rip.")\n'
        "    else:\n"
        '        input("Insert CD, wait for spin-up, then press Enter to continue... ")',
        "stage() notify")

    src = apply(src,
        '        if "--unknown" in extra_args:\n'
        '            log.error("whipper rip failed even with --unknown. Giving up.")\n'
        "            sys.exit(1)\n"
        "        if non_interactive:\n"
        "            retry = True\n"
        '            log.warning("whipper rip failed. Non-interactive mode: auto-retrying "\n'
        '                        "with --unknown once.")\n'
        "        else:",
        '        if "--unknown" in extra_args:\n'
        '            log.error("whipper rip failed even with --unknown. Giving up.")\n'
        '            notify.notify(log, "cdrip: whipper rip failed even with --unknown. Giving up.")\n'
        "            sys.exit(1)\n"
        "        if non_interactive:\n"
        "            # Phase 4: try a remote decision (actionable push, wait for a\n"
        "            # phone tap) before falling back to the old blind auto-retry.\n"
        "            decision = notify.ask(\n"
        "                log, cfg,\n"
        '                "cdrip: whipper rip failed (disc maybe not in MusicBrainz). "\n'
        '                "Retry with --unknown?",\n'
        '                [("RETRY", "Retry with --unknown"), ("GIVE_UP", "Give up")],\n'
        "            )\n"
        '            if decision == "RETRY":\n'
        "                retry = True\n"
        '            elif decision == "GIVE_UP":\n'
        "                retry = False\n"
        "            else:\n"
        "                retry = True\n"
        '                log.warning("No remote decision available (Telegram not configured, send "\n'
        '                            "failed, or no response in time). Non-interactive default: "\n'
        '                            "auto-retrying with --unknown once.")\n'
        "        else:",
        "rip() decision")

    src = apply(src,
        "        result = run(cmd, log, dry_run, cwd=staging)\n"
        "        if result.returncode != 0:\n"
        '            log.error("whipper rip failed again with --unknown. Giving up.")\n'
        "            sys.exit(1)",
        "        result = run(cmd, log, dry_run, cwd=staging)\n"
        "        if result.returncode != 0:\n"
        '            log.error("whipper rip failed again with --unknown. Giving up.")\n'
        '            notify.notify(log, "cdrip: whipper rip failed again with --unknown. Giving up.")\n'
        "            sys.exit(1)",
        "rip() second failure notify")

    src = apply(src,
        "    if album_path is None:\n"
        '        log.error(f"Could not find any folder containing .flac files under {staging}. "\n'
        '                  "Leaving staging as-is for manual inspection.")\n'
        "        sys.exit(1)\n"
        '    log.info(f"Rip staged at: {album_path}")\n'
        "    return album_path",
        "    if album_path is None:\n"
        '        log.error(f"Could not find any folder containing .flac files under {staging}. "\n'
        '                  "Leaving staging as-is for manual inspection.")\n'
        '        notify.notify(log, "cdrip: could not find the ripped album folder after "\n'
        '                      "whipper finished. Check arch-box.")\n'
        "        sys.exit(1)\n"
        '    log.info(f"Rip staged at: {album_path}")\n'
        '    notify.notify(log, f"cdrip: rip verified, now tagging: {os.path.basename(album_path)}")\n'
        "    return album_path",
        "rip() staged notify")

    src = apply(src,
        "        if non_interactive:\n"
        '            log.error("Non-interactive mode: stopping rather than guessing whether "\n'
        '                      "to continue.")\n'
        "            sys.exit(1)\n"
        "        confirm = input(",
        "        if non_interactive:\n"
        '            log.error("Non-interactive mode: stopping rather than guessing whether "\n'
        '                      "to continue.")\n'
        '            notify.notify(log, "cdrip: beet import failed (non-zero exit). Stopped -- "\n'
        '                          "check the log on arch-box.")\n'
        "            sys.exit(1)\n"
        "        confirm = input(",
        "import_beets() failure notify")

    src = apply(src,
        '            log.error("Stopping at your request.")\n'
        "            sys.exit(1)\n\n"
        "# ---------------------------------------------------------------------------\n"
        "# Step 4: copy log + cue (extrafiles workaround)",
        '            log.error("Stopping at your request.")\n'
        "            sys.exit(1)\n\n"
        "    if not dry_run:\n"
        '        notify.notify(log, f"cdrip: tagged, fetching art next: {os.path.basename(album_path)}")\n\n'
        "# ---------------------------------------------------------------------------\n"
        "# Step 4: copy log + cue (extrafiles workaround)",
        "import_beets() tagged notify")

    src = apply(src,
        "    elif candidates:\n"
        "        if non_interactive:\n"
        '            log.error(f"Non-interactive mode: {len(candidates)} candidate destination "\n'
        '                      f"folders found, can\'t disambiguate: {candidates}")\n'
        "            sys.exit(1)\n"
        '        print("Multiple recently-modified album folders found:")',
        "    elif candidates:\n"
        "        if non_interactive:\n"
        "            # Phase 4: with exactly 2 candidates, offer both as tappable\n"
        "            # buttons. With more than that, fall through to fail-loudly.\n"
        "            if len(candidates) == 2:\n"
        "                decision = notify.ask(\n"
        "                    log, cfg,\n"
        '                    "cdrip: 2 possible destination folders found, which is correct?\\n"\n'
        '                    f"1: {candidates[0]}\\n2: {candidates[1]}",\n'
        '                    [("FOLDER_1", os.path.basename(candidates[0])),\n'
        '                     ("FOLDER_2", os.path.basename(candidates[1]))],\n'
        "                )\n"
        '                if decision == "FOLDER_1":\n'
        "                    return candidates[0]\n"
        '                if decision == "FOLDER_2":\n'
        "                    return candidates[1]\n"
        '                log.warning("No remote decision available for destination folder choice.")\n'
        '            log.error(f"Non-interactive mode: {len(candidates)} candidate destination "\n'
        '                      f"folders found, can\'t disambiguate: {candidates}")\n'
        '            notify.notify(log, f"cdrip: {len(candidates)} possible destination folders, "\n'
        '                          "can\'t disambiguate. Check arch-box.")\n'
        "            sys.exit(1)\n"
        '        print("Multiple recently-modified album folders found:")',
        "find_dest_folder() decision")

    src = apply(src,
        "    elif non_interactive:\n"
        '        log.error("Non-interactive mode: no destination folder candidates found.")\n'
        "        sys.exit(1)\n"
        '    return input("Enter the destination album folder path: ").strip()',
        "    elif non_interactive:\n"
        '        log.error("Non-interactive mode: no destination folder candidates found.")\n'
        '        notify.notify(log, "cdrip: no destination folder candidates found after import. "\n'
        '                      "Check arch-box.")\n'
        "        sys.exit(1)\n"
        '    return input("Enter the destination album folder path: ").strip()',
        "find_dest_folder() no-candidates notify")

    src = apply(src,
        "        resp = requests.get(url, params=params, timeout=10)\n"
        "        resp.raise_for_status()\n"
        '        log.info(f"Navidrome rescan triggered: {resp.json()}")\n'
        "    except requests.RequestException as e:\n"
        '        log.error(f"Navidrome rescan failed: {e}")',
        "        resp = requests.get(url, params=params, timeout=10)\n"
        "        resp.raise_for_status()\n"
        '        log.info(f"Navidrome rescan triggered: {resp.json()}")\n'
        '        notify.notify(log, "cdrip: Navidrome rescan triggered, wrapping up.")\n'
        "    except requests.RequestException as e:\n"
        '        log.error(f"Navidrome rescan failed: {e}")\n'
        '        notify.notify(log, f"cdrip: Navidrome rescan failed: {e}")',
        "trigger_navidrome_rescan() notify")

    src = apply(src,
        '    log.info("Remaining checklist items to confirm by ear/eye:")\n'
        '    log.info("  - Tags populated (album, albumartist, year, track, title)")\n'
        '    log.info("  - Album visible and playable in Navidrome")\n'
        "    return ok",
        '    log.info("Remaining checklist items to confirm by ear/eye:")\n'
        '    log.info("  - Tags populated (album, albumartist, year, track, title)")\n'
        '    log.info("  - Album visible and playable in Navidrome")\n\n'
        '    status = "complete" if ok else "finished with issues -- check logs on arch-box"\n'
        '    notify.notify(log, f"cdrip: {status}: {os.path.basename(dest_folder)}")\n\n'
        "    return ok",
        "validate() final notify")

    TARGET.write_text(src)
    print("patched ripper_orchestrator.py")
    print("done")


if __name__ == "__main__":
    main()
