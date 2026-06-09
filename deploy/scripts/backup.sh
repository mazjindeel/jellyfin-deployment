#!/bin/bash
set -euo pipefail

HOMELAB_ROOT="${HOMELAB_ROOT:-$HOME/homelab}"
CONFIG_DIR="${HOMELAB_DATA:-${HOMELAB_ROOT}/data/jellyfin}/config"
BACKUP_DIR="${BACKUP_DIR:-${HOMELAB_ROOT}/data/jellyfin/backups}"
S3_BUCKET="${S3_BACKUP_BUCKET:-mealie-backups.cookbook.mazjindeel.com}"
S3_PREFIX="${S3_PREFIX:-jellyfin}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-7}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="jellyfin_config_${TIMESTAMP}.tar.gz"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_FILE}"

export PATH="${HOME}/.local/bin:${PATH}"

mkdir -p "${BACKUP_DIR}"

if [ ! -d "${CONFIG_DIR}" ]; then
  echo "ERROR: Jellyfin config dir not found: ${CONFIG_DIR}"
  exit 1
fi

echo "Starting Jellyfin config backup at $(date)"
tar czf "${BACKUP_PATH}" -C "$(dirname "${CONFIG_DIR}")" "$(basename "${CONFIG_DIR}")"

if [ -n "${S3_BUCKET}" ]; then
  echo "Uploading backup to s3://${S3_BUCKET}/${S3_PREFIX}/${BACKUP_FILE}"
  aws s3 cp "${BACKUP_PATH}" "s3://${S3_BUCKET}/${S3_PREFIX}/${BACKUP_FILE}"
fi

find "${BACKUP_DIR}" -name "jellyfin_config_*.tar.gz" -mtime +"${LOCAL_RETENTION_DAYS}" -delete
echo "Jellyfin backup completed at $(date)"
