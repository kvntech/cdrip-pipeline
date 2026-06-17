# Phase 1 Runbook: Manual CD Rip to Tagged FLAC on NAS

Status: draft v1, reconstructed from Phase 0. Lines marked [VERIFY ON LIVE RIP]
must be confirmed during the next real rip before this runbook is considered final.

## Purpose
Exact, repeatable manual procedure that takes a CD from insertion to a tagged FLAC
album in the Navidrome library. This is the spec the Phase 2 Python orchestrator
will implement.

## Environment
- Host: arch-box (EndeavourOS, ThinkCentre M700 Tiny), 192.168.8.6
- Drive: LG GP65NB60 external USB, device /dev/sr0
- whipper config: ~/.config/whipper/whipper.conf (read offset +6, cache defeat on)
- beets: 2.8.0 on Python 3.14.3
  plugins: chroma, embedart, extrafiles, fetchart, inline, replaygain, scrub
- Staging (local): ~/cd-rips/staging/
- Library (NFS from TrueNAS, Maproot=apps): /mnt/tank/music/library/FLAC/CDRips/
- Navidrome scan root: /mnt/tank/music/library/

## Pre-flight checks
1. NFS mounted:        mountpoint /mnt/tank/music/library
   If not, wake the automount: ls /mnt/tank/music/library
2. Drive present:      ls -l /dev/sr0
3. whipper sees drive: whipper drive list
4. beets sane:         beet version   (confirm 7 plugins load)

## Procedure

### Step 1: Stage
    mkdir -p ~/cd-rips/staging
    cd ~/cd-rips/staging
Insert CD, wait ~10s for spin-up.

### Step 2: Rip with whipper
    whipper cd rip
whipper reads the TOC, queries MusicBrainz + AccurateRip, rips and encodes FLAC,
verifies against AccurateRip, and writes a per-disc folder containing the FLACs
plus a .log and .cue.
Expected: all tracks AccurateRip verified (Phase 0 test rip of Grant Green's
"Feelin' the Spirit" hit confidence 17-18).
[VERIFY ON LIVE RIP] exact flags beyond `cd rip`, and the exact folder-name
pattern whipper produces in staging.

### Step 3: Import + tag with beets
beets must run as root because the NFS export uses Maproot=apps; non-root writes
are refused.
[VERIFY ON LIVE RIP] config-under-sudo gotcha: `sudo beet` reads
/root/.config/beets/, not your user config. Confirm the form that worked in
Phase 0, likely an explicit config path:
    sudo beet -c ~/.config/beets/config.yaml import ~/cd-rips/staging/<album-folder>

Known issue A (MusicBrainz autotagger): import may return zero candidates despite
whipper writing valid disc IDs (suspected beets 2.8.0 / Python 3.14 / musicbrainzngs
compatibility). Workaround options to test and then standardize:
  - At the prompt, choose "Enter Id" and paste the MusicBrainz release ID or URL
    from the album's MB page.
  - Or import with existing tags and correct later.
[VERIFY ON LIVE RIP] which workaround becomes the standard step.

### Step 4: Copy log + cue (extrafiles workaround)
Known issue B: beets-extrafiles crashes on cli_exit, so .log/.cue are NOT copied
automatically. Copy them manually into the destination album folder on the NAS:
    sudo cp ~/cd-rips/staging/<album-folder>/*.log \
            ~/cd-rips/staging/<album-folder>/*.cue \
            "/mnt/tank/music/library/FLAC/CDRips/<Artist>/<Year - Album>/"
[VERIFY ON LIVE RIP] confirm sudo is required here (expected yes, same Maproot reason).

### Step 5: Trigger Navidrome rescan
[VERIFY ON LIVE RIP] current method: auto-scan interval vs manual trigger via the
Navidrome UI/API in LXC 106 (192.168.8.60).

### Step 6: Clean staging
After the album is confirmed in the library and playing:
    rm -rf ~/cd-rips/staging/<album-folder>

## Validation checklist
- [ ] FLACs at /mnt/tank/music/library/FLAC/CDRips/<Artist>/<Year - Album>/
- [ ] Filenames match: NN - Artist - Title.flac (single disc)
- [ ] Tags populated: album, albumartist, year, track, title
- [ ] .log and .cue present in album folder
- [ ] Album visible and playable in Navidrome

## Open items to resolve on next live rip
1. Exact whipper invocation and staging folder-name pattern.
2. beets config-under-sudo: settle the canonical command.
3. MusicBrainz zero-candidates: pick and document the standard workaround.
4. Confirm sudo needed for the log/cue copy.
5. Navidrome rescan trigger method.
