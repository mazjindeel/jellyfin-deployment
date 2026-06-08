# Future work — remote Jellyfin access

LAN-only today. To expose Jellyfin outside the home network later:

## home-server changes

1. Add `jellyfin.mazjindeel.com` to `ROUTE53_RECORDS` in `home-server/.env`.
2. Add `home-server/nginx/conf.d/jellyfin-ssl.conf` (proxy to `http://jellyfin:8096` on the shared `homelab` Docker network — see [home-server/docs/DOCKER_NETWORK.md](../home-server/docs/DOCKER_NETWORK.md)).
3. Add `-d jellyfin.mazjindeel.com` to `home-server/scripts/obtain-certs.sh`.
4. Obtain or restore TLS cert, run `deploy-homelab.sh` or restart nginx: `systemctl --user restart homelab-nginx`.

No new router port forwards — traffic enters on 443 through nginx.

## jellyfin-deployment changes

Remove the host port mapping so nginx is the sole WAN entry (keep Jellyfin on `homelab` network):

```yaml
# Remove ports: — nginx reaches jellyfin:8096 on the homelab network
networks:
  - homelab
```

LAN access would then be via `https://jellyfin.mazjindeel.com` (or nginx on LAN IP with Host header).

## Jellyfin dashboard

1. Enable **Allow remote access**.
2. Set **Published Server URL** to `https://jellyfin.mazjindeel.com`.
3. Add Docker bridge subnet to **Known Proxies** if needed.
