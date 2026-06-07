# Future work — remote Jellyfin access

LAN-only today. To expose Jellyfin outside the home network later:

## home-server changes

1. Add `jellyfin.mazjindeel.com` to `ROUTE53_RECORDS` in `home-server/.env`.
2. Add `home-server/nginx/conf.d/jellyfin-ssl.conf` (proxy to `host.docker.internal:8096`, same pattern as Mealie).
3. Add `-d jellyfin.mazjindeel.com` to `home-server/scripts/obtain-certs.sh`.
4. Obtain or restore TLS cert, restart nginx: `systemctl --user restart homelab-nginx`.

No new router port forwards — traffic enters on 443 through nginx.

## jellyfin-deployment changes

Optionally bind Jellyfin to localhost only so nginx is the sole entry point:

```yaml
ports:
  - "127.0.0.1:8096:8096"
```

## Jellyfin dashboard

1. Enable **Allow remote access**.
2. Set **Published Server URL** to `https://jellyfin.mazjindeel.com`.
3. Add Docker bridge subnet to **Known Proxies** if needed.
