#!/usr/bin/env bash
# Prove the newest backup can be restored (OPS-02): load it into a scratch database next to the live one,
# compare row counts of the tables that matter, then drop the scratch database. The live database is only read.
#
#   swm-restore-test.sh                 # newest dump in $BACKUP_DIR/db
#   swm-restore-test.sh path/to.dump    # a specific dump
#
# Same settings as swm-backup.sh (BACKUP_DIR, PG_CONTAINER, PG_DB, PG_USER; /etc/swm-backup.conf).
set -euo pipefail

[ -f /etc/swm-backup.conf ] && . /etc/swm-backup.conf
BACKUP_DIR=${BACKUP_DIR:-/opt/swm-backups}
PG_CONTAINER=${PG_CONTAINER:-swm-postgres}
PG_DB=${PG_DB:-$(docker exec "$PG_CONTAINER" printenv POSTGRES_DB)}
PG_USER=${PG_USER:-$(docker exec "$PG_CONTAINER" printenv POSTGRES_USER)}
SCRATCH=swm_restore_test

dump=${1:-$(ls -1t "$BACKUP_DIR"/db/*.dump 2>/dev/null | head -1)}
[ -n "$dump" ] && [ -f "$dump" ] || { echo "no dump found in $BACKUP_DIR/db"; exit 1; }
[ "$SCRATCH" != "$PG_DB" ] || { echo "refusing: scratch name equals the live database"; exit 1; }

psql() { docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -v ON_ERROR_STOP=1 -qtA "$@"; }
cleanup() { psql -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" > /dev/null || true; }
trap cleanup EXIT

echo "restoring $(basename "$dump") into $SCRATCH"
cleanup
psql -d postgres -c "CREATE DATABASE $SCRATCH" > /dev/null
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$SCRATCH" --no-owner --exit-on-error < "$dump"

status=0
printf '%-20s %10s %10s\n' table backup live
for t in branches admins members givings sermons events unit_reports branch_reports site_settings; do
  b=$(psql -d "$SCRATCH" -c "SELECT count(*) FROM $t" 2>/dev/null || echo missing)
  l=$(psql -d "$PG_DB" -c "SELECT count(*) FROM $t" 2>/dev/null || echo missing)
  printf '%-20s %10s %10s\n' "$t" "$b" "$l"
  # The live database may have grown since the dump; fewer live rows, or a missing table, needs a look.
  if [ "$b" = missing ] || { [ "$l" != missing ] && [ "$l" -lt "$b" ]; }; then status=1; fi
done
[ $status -eq 0 ] && echo "restore test passed" || echo "restore test: CHECK THE COUNTS ABOVE"
exit $status
