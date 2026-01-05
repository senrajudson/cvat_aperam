#!/bin/sh
set -eu

echo "[$(date)] container cvat_backup iniciou"
echo "[$(date)] CVAT_BASE_URL=$CVAT_BASE_URL"
echo "[$(date)] STARTUP_SLEEP=${STARTUP_SLEEP:-120} INTERVAL_SEC=${INTERVAL_SEC:-86400} KEEP_LAST=${KEEP_LAST:-15}"

mkdir -p /backups

sleep "${STARTUP_SLEEP:-120}"

until curl -sf "$CVAT_BASE_URL/api/server/about" >/dev/null; do
  echo "[$(date)] aguardando CVAT responder em $CVAT_BASE_URL..."
  sleep 5
done

while true; do
  echo "[$(date)] iniciando backup CVAT..."
  python /app/main.py || echo "[$(date)] ERRO no backup (ver logs acima)"

  ls -1dt /backups/*/ 2>/dev/null | tail -n +"$(( ${KEEP_LAST:-15} + 1 ))" | xargs -r rm -rf

  echo "[$(date)] próximo backup em ${INTERVAL_SEC:-86400}s"
  sleep "${INTERVAL_SEC:-86400}"
done
