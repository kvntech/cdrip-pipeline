# Phase 1 Runbook: Manual CD Rip to Tagged FLAC on NAS

Status: v2, confirmed via three live rips on 2026-07-26 (Ry Cooder & Manuel
Galbán — Mambo sinuendo; Reflection Eternal — Train of Thought; John Coltrane
— Live at Birdland), run through the Phase 2 orchestrator. All items
originally marked [VERIFY ON LIVE RIP] are resolved below except the
Navidrome rescan trigger, which is still untested (rescan is disabled in the
orchestrator config pending that verification).

## Purpose
Exact, repeatable manual procedure that takes a CD from insertion to a tagged FLAC
album in the Navidrome library. This is the spec the Phase 2 Python orchestrator
implements (see `ripper_orchestrator.py`).

## Environment
- Host: arch-box (EndeavourOS, ThinkCentre M700 Tiny), 192.168.8.6
- Drive: LG GP65NB60 external USB, device /dev/sr0
- whipper config: ~/.config/whipper/whipper.conf (read offset +6, cache defeat on)
- beets: 2.8.0 on Python 3.14.3
  plugins: chroma, embedart, fetchart, inline, musicbrainz, replaygain, scrub
  (`extrafiles` removed — see Known issue B; `musicbrainz` added and a bogus
  `search_ids` import-config line removed — see Known issue A)
- Staging (local): ~/cd-rips/staging/
- Library (NFS from TrueNAS, Maproot=apps): /mnt/tank/music/library/FLAC/CDRips/
- Navidrome scan root: /mnt/tank/music/library/

## Pre-flight checks
1. **NFS mounted:** `findmnt /mnt/tank/music` — check the mount unit's actual
   target, **not** `/mnt/tank/music/library`. `library` is a subfolder inside
   the mount, not the mountpoint itself; `os.path.ismount()` (and `mountpoint`)
   only ever return true for the exact path fstab mounts at. Confirmed fstab
   entry:
       192.168.8.20:/mnt/tank/music  /mnt/tank/music  nfs  defaults,_netdev,nofail,x-systemd.automount,x-systemd.idle-timeout=600  0  0
   If the automount unit doesn't show up in
   `systemctl list-units --type=mount,automount`, it likely just hasn't been
   (re)generated since the fstab entry was added/changed — run
   `sudo systemctl daemon-reload`, then `sudo mount /mnt/tank/music` to
   confirm it actually mounts.
2. Drive present:      ls -l /dev/sr0
3. whipper sees drive: whipper drive list
4. beets sane:         beet version   (confirm 7 plugins load: chroma, embedart,
   fetchart, inline, musicbrainz, replaygain, scrub)

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

**Confirmed folder-name pattern:** whipper does NOT create the per-disc folder
directly in staging. It first creates a fixed intermediate wrapper directory,
then the real `Artist - Title` folder one level inside that — e.g.
`~/cd-rips/staging/album/Ry Cooder & Manuel Galbán - Mambo sinuendo/` or
`~/cd-rips/staging/live/John Coltrane - Live at Birdland/`. **The wrapper
folder's name varies** (`album`, `live`, and presumably others depending on
some internal whipper categorization) — don't hardcode a name; detect it by
descending through any chain of single-subdirectory wrappers until reaching a
folder that actually contains files. (This is what
`ripper_orchestrator.py`'s `rip()` does.)

Expected: all tracks AccurateRip verified (Phase 0 test rip of Grant Green's
"Feelin' the Spirit" hit confidence 17-18).

### Step 3: Import + tag with beets
beets must run as root because the NFS export uses Maproot=apps; non-root writes
are refused. **Confirmed working form** (explicit `-c` correctly overlays the
user config rather than falling back to `/root/.config/beets/`):
    sudo beet -c ~/.config/beets/config.yaml import ~/cd-rips/staging/<wrapper>/<Artist - Title>

**Known issue A (MusicBrainz autotagger) — RESOLVED 2026-07-27.** Import used
to return "No matching release found" even when manually pasting the correct
release ID at the "Enter Id" prompt. Root-caused via `beet -vv import` plus a
direct `musicbrainzngs.search_releases()` call outside of beets (which returned
real candidates fine, proving the network/API layer was never the problem):
(1) the `musicbrainz` plugin was missing from `plugins:` entirely, so beets had
zero registered metadata sources; (2) a bogus `search_ids: [mb_albumid,
mb_trackid, mb_releasetrackid]` line under `import:` was passing those literal
field names to beets as if they were real MBIDs, causing `Invalid MBID
(mb_albumid)` errors. **Fix:** add `musicbrainz` to `plugins:`, delete the
`search_ids` line. Confirmed live: the same album that used to fail now gets a
strong automatic ID match (93.7%, MusicBrainz release
`2c1109bb-777d-4a8c-8e9a-ad9f7d829441`) with no manual intervention. **"Use
as-is" is no longer needed for new imports** — it remains a valid fallback only
for discs genuinely absent from MusicBrainz (e.g. Dizzy Gillespie y Machito,
where a new MusicBrainz release entry had to be created from scratch).

### Step 4: Copy log + cue (extrafiles removed)
**Known issue B — confirmed live, exactly as suspected.** beets-extrafiles
crashes on the `cli_exit` hook with
`AttributeError: module 'beets.library' has no attribute 'DefaultTemplateFunctions'`.
Confirmed this crash happens *after* the real audio import/move already
succeeds — it only breaks the plugin's own post-import log/cue copy step, not
the rip itself. **Permanent fix applied: `extrafiles` removed from the beets
config's plugins list entirely.** The orchestrator copies `.log`/`.cue`
manually instead:
    sudo cp ~/cd-rips/staging/<wrapper>/<Artist - Title>/*.log \
            ~/cd-rips/staging/<wrapper>/<Artist - Title>/*.cue \
            "/mnt/tank/music/library/FLAC/CDRips/<Artist>/<Year - Album>/"
**Confirmed: sudo is required** for this copy (same Maproot reason as Step 3).

### Step 5: Fetch + embed album art (new — Known issue C)
**Discovered live:** `fetchart`/`embedart` do not fire automatically as a side
effect of a "Use as-is" import, even though both plugins work correctly when
invoked directly afterward. Run explicitly, targeting the album via a beets
path query (confirmed working syntax):
    sudo beet -c ~/.config/beets/config.yaml fetchart "path:/mnt/tank/music/library/FLAC/CDRips/<Artist>/<Year - Album>"
This fetches art (saved as `cover.jpg` in the album folder) and embedding into
the FLAC tags happens automatically via embedart's hook on fetchart's result
— confirmed via `metaflac --list --block-type=PICTURE` showing a real
embedded JPEG.

### Step 6: Trigger Navidrome rescan
**Still open / untested.** Not yet exercised on a live rip; disabled in the
orchestrator config (`navidrome.enabled: false`) until verified.
[VERIFY ON LIVE RIP] current method: auto-scan interval vs manual trigger via
the Navidrome UI/API in LXC 106 (192.168.8.60).

### Step 7: Clean staging
After the album is confirmed in the library and playing:
    rm -rf ~/cd-rips/staging/<wrapper>/<Artist - Title>
Note: deleting just the per-disc folder (not the whole wrapper) is fine —
whipper recreates the wrapper folder itself on the next rip regardless of
whether it's empty or absent.

## Validation checklist
- [x] FLACs at /mnt/tank/music/library/FLAC/CDRips/<Artist>/<Year - Album>/
- [x] Filenames match: NN - Artist - Title.flac (single disc)
- [ ] Tags populated: album, albumartist, year, track, title — visually spot-checked
      on the NAS listing, not yet verified field-by-field
- [x] .log and .cue present in album folder
- [x] Cover art present (file + embedded in FLAC tags)
- [ ] Album visible and playable in Navidrome — not yet confirmed (rescan step
      still open, see Step 6)

## Open items
1. ~~Exact whipper invocation and staging folder-name pattern.~~ Resolved (Step 2).
2. ~~beets config-under-sudo: settle the canonical command.~~ Resolved (Step 3).
3. ~~MusicBrainz zero-candidates: root-caused and fixed.~~ Resolved 2026-07-27 —
   missing `musicbrainz` plugin + bogus `search_ids` config key (Step 3). Automatic
   matching now works; Use as-is is a fallback only, not the standard path.
4. ~~Confirm sudo needed for the log/cue copy.~~ Resolved, yes (Step 4).
5. **Navidrome rescan trigger method.** Still open.
6. **Tag-field-level validation and Navidrome playback confirmation.** Not yet
   done on a live album; worth a manual pass once Navidrome rescan is sorted.
