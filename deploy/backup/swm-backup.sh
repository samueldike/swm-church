#!/usr/bin/env bash
# Nightly backup of the SWM platform (OPS-02): database dump + uploaded files + server .env, kept KEEP_DAYS days,
# copied off the server when RCLONE_REMOTE is set. Safe to run by hand at any time; it only reads from the app.
#
#   install:  cp deploy/backup/swm-backup.sh /usr/local/bin/ && chmod 700 /usr/local/bin/swm-backup.sh
#   cron:     15 2 * * *  root  /usr/local/bin/swm-backup.sh >> /var/log/swm-backup.log 2>&1
#
# Settings (environment, or /etc/swm-backup.conf in the same KEY=value form):
#   BACKUP_DIR      where backups are kept on this server            (default /opt/swm-backups)
#   KEEP_DAYS       how many days of database dumps to keep           (default 14)
#   PG_CONTAINER    the Postgres container                            (default swm-postgres)
#   PG_DB, PG_USER  database and user (default: read from the container's POSTGRES_DB / POSTGRES_USER)
#   APP_CONTAINER   container whose file mounts are backed up         (default swm-app)
#   FILE_MOUNTS     container paths to back up        (default "/app/media /app/telegram-archive /app/app/static/images")
#   APP_DIR         directory holding the server's .env               (default /opt/swm)
#   RCLONE_REMOTE   off-site target, e.g. "b2:swm-backups" (an rclone remote; use a "crypt" remote: .env holds secrets)
set -euo pipefail

[ -f /etc/swm-backup.conf ] && . /etc/swm-backup.conf
BACKUP_DIR=${BACKUP_DIR:-/opt/swm-backups}
KEEP_DAYS=${KEEP_DAYS:-14}
PG_CONTAINER=${PG_CONTAINER:-swm-postgres}
APP_CONTAINER=${APP_CONTAINER:-swm-app}
FILE_MOUNTS=${FILE_MOUNTS:-"/app/media /app/telegram-archive /app/app/static/images"}
APP_DIR=${APP_DIR:-/opt/swm}
RCLONE_REMOTE=${RCLONE_REMOTE:-}

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() { log "FAILED: $*"; exit 1; }

PG_DB=${PG_DB:-$(docker exec "$PG_CONTAINER" printenv POSTGRES_DB)} || fail "cannot read POSTGRES_DB from $PG_CONTAINER"
PG_USER=${PG_USER:-$(docker exec "$PG_CONTAINER" printenv POSTGRES_USER)} || fail "cannot read POSTGRES_USER from $PG_CONTAINER"
stamp=$(date -u +%Y%m%d-%H%M)
umask 077
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/files" "$BACKUP_DIR/env"

# 1. Database: custom-format dump (compressed, restorable table by table), then prove it can be read back.
dump="$BACKUP_DIR/db/${PG_DB}-${stamp}.dump"
log "dumping $PG_DB from $PG_CONTAINER"
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$dump.part" || { rm -f "$dump.part"; fail "pg_dump"; }
docker exec -i "$PG_CONTAINER" pg_restore --list < "$dump.part" > /dev/null || { rm -f "$dump.part"; fail "dump is unreadable"; }
mv "$dump.part" "$dump"
log "database: $dump ($(du -h "$dump" | cut -f1))"

# 2. Uploaded files: mirror each mount's host directory (incremental; audio is large and rarely changes).
for mount in $FILE_MOUNTS; do
  src=$(docker inspect -f "{{range .Mounts}}{{if eq .Destination \"$mount\"}}{{.Source}}{{end}}{{end}}" "$APP_CONTAINER" 2>/dev/null || true)
  if [ -z "$src" ] || [ ! -d "$src" ]; then log "files: $mount is not mounted in $APP_CONTAINER, skipped"; continue; fi
  name=$(echo "$mount" | tr '/' '_' | sed 's/^_//')
  mkdir -p "$BACKUP_DIR/files/$name"
  rsync -a --delete "$src/" "$BACKUP_DIR/files/$name/" || fail "rsync $src"
  log "files: $mount <- $src ($(du -sh "$BACKUP_DIR/files/$name" | cut -f1))"
done

# 3. The server .env: without its SECRET_KEY every key saved in Settings is unreadable after a restore.
if [ -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env" "$BACKUP_DIR/env/env-${stamp}"
  log "env: copied $APP_DIR/.env"
fi

# 4. Retention (local).
find "$BACKUP_DIR/db" -name '*.dump' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR/env" -name 'env-*' -mtime +"$KEEP_DAYS" -delete

# 5. Off-site copy: a backup on the same disk does not survive the disk.
if [ -n "$RCLONE_REMOTE" ]; then
  rclone copy "$BACKUP_DIR/db" "$RCLONE_REMOTE/db" --max-age 48h || fail "rclone db"
  rclone sync "$BACKUP_DIR/files" "$RCLONE_REMOTE/files" || fail "rclone files"
  rclone copy "$BACKUP_DIR/env" "$RCLONE_REMOTE/env" --max-age 48h || fail "rclone env"
  rclone delete "$RCLONE_REMOTE/db" --min-age "${KEEP_DAYS}d" || true
  log "off-site: copied to $RCLONE_REMOTE"
else
  log "WARNING: RCLONE_REMOTE is not set; backups exist only on this server"
fi

date -u +%FT%TZ > "$BACKUP_DIR/last-success"
log "backup complete"
