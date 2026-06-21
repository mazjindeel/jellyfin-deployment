# Jellyfin Deployment

LAN-only Jellyfin stack for the basement ThinkPad homelab.

**URL (LAN):** http://192.168.63.2:8096

Shared infrastructure (nginx, TLS, DDNS, recovery docs) lives in **[home-server](../home-server/)**. Remote access is future work — see [FUTURE_WORK.md](FUTURE_WORK.md).

## This repo contains

| Path | Purpose |
|------|---------|
| `docker-compose.prod.yml` | Production Jellyfin container |
| `.env.prod.example` | Production env template |
| `deploy/systemd/jellyfin.service` | User systemd unit (auto-start on boot) |
| `scripts/organize-media.py` | Organize `~/stuff/media` into Jellyfin-friendly paths (dry-run by default) |
| `scripts/simulate-from-paths.py` | Dry-run the organizer from a `find` listing (no files required) |
| `scripts/README.md` | **Script docs** — usage, architecture, extension guide for agents |
| `.cursor/skills/process-media-downloads/` | **Cursor skill** — completed-downloads → library workflow (dry-run → approve → apply) |
| `FINAL_PLAN.md` | Last reviewed dry-run plan for the full library |

## Production (on ThinkPad)

```bash
cp .env.prod.example .env.prod
mkdir -p ~/homelab/data/jellyfin/{config,cache}
~/homelab/home-server/scripts/ensure-homelab-network.sh
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Uses the shared **`homelab`** Docker network (same as Mealie/RSS/nginx). WAN access is future work — see [FUTURE_WORK.md](FUTURE_WORK.md).

Systemd is installed via `home-server/scripts/install-systemd.sh`.

Media files go in `~/stuff/media` on the ThinkPad (created by `install-systemd.sh` if missing). Audiobooks go in `~/stuff/audiobooks` (same bootstrap).

### Audiobooks (listening)

**Jellyfin is not recommended for audiobook playback on mobile** — background audio stops when the phone locks. Use **[Audiobookshelf](https://www.audiobookshelf.org/)** instead; see the homelab plan: [AUDIOBOOK-PLATFORM-PLAN.md](../home-server/docs/AUDIOBOOK-PLATFORM-PLAN.md).

- **Files:** `organize-media.py` still targets `~/stuff/audiobooks` (Author/Book layout)
- **Listening:** iPhone → ShelfPlayer; Android → official ABS app (see plan doc for setup)
- **Jellyfin Books library:** optional for browsing only; same files on disk

### Organizing media

The library organizer targets Jellyfin’s preferred folder layout for TV/movies and an Author/Book layout for audiobooks. It dry-runs by default — see **[scripts/README.md](scripts/README.md)** for usage, safety notes, and how to extend parsing/canonicalization.

Quick start on the ThinkPad:

```bash
python3 ~/jellyfin-deployment/scripts/organize-media.py ~/stuff/media          # dry-run video
python3 ~/jellyfin-deployment/scripts/organize-media.py ~/stuff/media --apply --cleanup-empty

# Completed downloads → both libraries
python3 ~/jellyfin-deployment/scripts/organize-media.py ~/stuff/downloads/completed \
  --output ~/stuff/media \
  --audiobooks-output ~/stuff/audiobooks
python3 ~/jellyfin-deployment/scripts/organize-media.py ~/stuff/downloads/completed \
  --output ~/stuff/media \
  --audiobooks-output ~/stuff/audiobooks \
  --apply --cleanup-empty
```

First-run setup at http://192.168.63.2:8096:

1. Create an admin account.
2. Add a library with path `/media` (maps to `~/stuff/media` on the host) for TV/movies.
3. Install the **Bookshelf** plugin (Dashboard → Plugins → Catalog), then add a **Books** library with path `/audiobooks` (maps to `~/stuff/audiobooks`) — optional; for mobile listening use Audiobookshelf instead ([plan](../home-server/docs/AUDIOBOOK-PLATFORM-PLAN.md)).
4. Dashboard → Networking → disable **Allow remote access** (LAN-only for now).
5. Dashboard → Playback → Transcoding → enable hardware acceleration (**QSV** / VAAPI). The compose file passes through `/dev/dri` (Intel Quick Sync on the i5-8250U). If transcoding fails with a permission error, run `getent group render` on the host and add `group_add: ["<GID>"]` to `docker-compose.prod.yml`.
