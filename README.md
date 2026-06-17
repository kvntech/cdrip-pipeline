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
- **Tagging:** beets (MusicBrainz lookup, tag normalization, file moves)
- **Orchestration:** Python script wrapping whipper + beets (this project)
- **Trigger:** udev rule on `/dev/sr0` insertion -> systemd service -> orchestrator
- **Library:** Navidrome (LXC 106) scanning the NAS music library

### Hardware

- **Host:** arch-box (EndeavourOS, ThinkCentre M700 Tiny, 192.168.8.6)
- **Drive:** LG GP65NB60 (external USB DVD drive)
- **NAS:** TrueNAS (192.168.8.20), tank pool (SSD)

### Paths

- **Staging (local on arch-box):** `~/cd-rips/staging/`
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
  - [x] Beets installed (pacman) + beets-extrafiles (pip, system-wide)
  - [x] Beets config: path templates, match thresholds, plugins (chroma, embedart, extrafiles, fetchart, inline, replaygain, scrub)
  - [x] Test rip: Grant Green - Feelin' the Spirit (Blue Note, 2005 reissue)
    - All 6 tracks AccurateRip-verified at confidence 17-18
    - Files landed at `/mnt/tank/music/library/FLAC/CDRips/Grant Green/2005 - Feelin' the Spirit/`
    - Naming pattern verified: `NN - Artist - Title.flac`
- [~] **Phase 1:** Document the exact CLI sequence for a manual rip (becomes the spec for Phase 2's orchestrator).
- [ ] **Phase 2:** Python orchestrator. Wrap whipper + beets in a single script with logging, config, error handling.
- [ ] **Phase 3:** Hands-off automation. udev rule + systemd service triggers the orchestrator on disc insertion.
- [ ] **Phase 4:** Polish. Notifications (ntfy / Home Assistant), TUI status, full documentation.

## Known issues (carried into later phases)

- **Beets autotagger returns "no candidates" for MusicBrainz lookups** despite whipper writing valid MusicBrainz IDs to FLAC tags. Confirmed via `-vv`: `Evaluating 0 candidates`. Network and release ID validated independently via curl. Likely a beets 2.8.0 / Python 3.14 / musicbrainzngs compatibility issue. Phase 0 workaround: import with "Use as-is" (whipper's tags are already production-quality).
- **beets-extrafiles plugin crashes on `cli_exit`** with `AttributeError: module 'beets.library' has no attribute 'DefaultTemplateFunctions'`. Plugin is out of sync with beets 2.8.0 internals. Audio import succeeds; the crash happens at the post-import log/cue copy step. Phase 2 alternative: have the Python orchestrator copy `.log`/`.cue` files alongside FLACs directly, eliminating the plugin dependency.
- **Permissions model:** beets must run as root for writes to land on the NAS (Maproot=apps on the NFS share remaps root → apps user, which owns the dataset). Aligns with Phase 3's systemd service model.

## Status

Phase 0 complete. Real end-to-end pipeline verified.
