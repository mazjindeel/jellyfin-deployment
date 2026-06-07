# Jellyfin Deployment

LAN-only Jellyfin stack for the basement ThinkPad homelab.

**URL (LAN):** http://192.168.1.50:8096

Shared infrastructure (nginx, TLS, DDNS, recovery docs) lives in **[home-server](../home-server/)**. Remote access is future work — see [FUTURE_WORK.md](FUTURE_WORK.md).

## This repo contains

| Path | Purpose |
|------|---------|
| `docker-compose.prod.yml` | Production Jellyfin container |
| `.env.prod.example` | Production env template |
| `deploy/systemd/jellyfin.service` | User systemd unit (auto-start on boot) |

## Production (on ThinkPad)

```bash
cp .env.prod.example .env.prod
mkdir -p ~/homelab/data/jellyfin/{config,cache}
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Systemd is installed via `home-server/scripts/install-systemd.sh`.

Media files go in `~/stuff/media` on the ThinkPad (created by `install-systemd.sh` if missing).

First-run setup at http://192.168.1.50:8096:

1. Create an admin account.
2. Add a library with path `/media` (maps to `~/stuff/media` on the host).
3. Dashboard → Networking → disable **Allow remote access** (LAN-only for now).
4. Dashboard → Playback → Transcoding → enable hardware acceleration (**QSV** / VAAPI). The compose file passes through `/dev/dri` (Intel Quick Sync on the i5-8250U). If transcoding fails with a permission error, run `getent group render` on the host and add `group_add: ["<GID>"]` to `docker-compose.prod.yml`.
