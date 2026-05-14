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

- [ ] **Phase 0:** Manual end-to-end. Install whipper + beets, mount NFS, characterize drive, rip one test CD manually.
- [ ] **Phase 1:** Manual orchestration. Document the exact CLI commands that produce a finished rip.
- [ ] **Phase 2:** Python orchestrator. Wrap whipper + beets in a single script with logging, config, error handling.
- [ ] **Phase 3:** Hands-off automation. udev rule + systemd service triggers the orchestrator on disc insertion.
- [ ] **Phase 4:** Polish. Notifications (ntfy / Home Assistant), TUI status, full documentation.

## Status

Currently in Phase 0.
