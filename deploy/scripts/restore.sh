#!/bin/bash
set -euo pipefail

usage() {
  echo "Usage: $0 <backup-file-or-s3-key>"
  echo "  Local:  $0 ~/homelab/data/jellyfin/backups/jellyfin_config_20260103.tar.gz"
  echo "  S3:     $0 s3://mealie-backups.cookbook.mazjindeel.com/jellyfin/jellyfin_config_20260103.tar.gz"
  exit 1
}

[ $# -eq 1 ] || usage

HOMELAB_ROOT="${HOMELAB_ROOT:-$HOME/homelab}"
CONFIG_PARENT="${HOMELAB_DATA:-${HOMELAB_ROOT}/data/jellyfin}"
BACKUP_SRC="$1"
TMP="/tmp/jellyfin_restore_$$.tar.gz"

export PATH="${HOME}/.local/bin:${PATH}"

if [[ "${BACKUP_SRC}" == s3://* ]]; then
  aws s3 cp "${BACKUP_SRC}" "${TMP}"
else
  cp "${BACKUP_SRC}" "${TMP}"
fi

read -r -p "Replace ${CONFIG_PARENT}/config with backup? [y/N] " confirm
if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
  rm -f "${TMP}"
  echo "Aborted."
  exit 1
fi

APP_DIR="${HOMELAB_ROOT}/jellyfin-deployment"
if docker ps --format '{{.Names}}' | grep -qx jellyfin; then
  cd "${APP_DIR}"
  docker compose -f docker-compose.prod.yml --env-file .env.prod stop jellyfin
fi

rm -rf "${CONFIG_PARENT}/config"
mkdir -p "${CONFIG_PARENT}"
tar xzf "${TMP}" -C "${CONFIG_PARENT}"
rm -f "${TMP}"

if [ -f "${APP_DIR}/.env.prod" ]; then
  cd "${APP_DIR}"
  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d jellyfin
fi

echo "Jellyfin config restore completed at $(date)"
