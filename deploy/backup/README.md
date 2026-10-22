# Backups (OPS-02)

What is saved every night, and why:

| What | Where it comes from | Why it matters |
|---|---|---|
| Database dump (`pg_dump -Fc`) | `swm-postgres` | Members, gifts, reports, settings: everything typed into the console. |
| Uploaded files | the host folders mounted into `swm-app` at `/app/media`, `/app/telegram-archive`, `/app/app/static/images` | Message audio, photos, sliders, Telegram archive. Not in the database. |
| Server `.env` | `/opt/swm/.env` | Its `SECRET_KEY` decrypts every key saved in Settings (gateways, Telegram, email, AI). Without it a restored site has no working keys. |

Dumps and `.env` copies are kept `KEEP_DAYS` (14) days. Files are mirrored (the copy follows deletions on the next run).

## Install on the server (one time)

```bash
apt-get install -y rsync rclone
install -m 700 deploy/backup/swm-backup.sh deploy/backup/swm-restore-test.sh /usr/local/bin/
rclone config                       # create the off-site remote, then a "crypt" remote on top of it
cat > /etc/swm-backup.conf <<'EOF'
RCLONE_REMOTE=swm-crypt:
EOF
chmod 600 /etc/swm-backup.conf
echo '15 2 * * * root /usr/local/bin/swm-backup.sh >> /var/log/swm-backup.log 2>&1' > /etc/cron.d/swm-backup
/usr/local/bin/swm-backup.sh        # first run by hand: read the log it prints
/usr/local/bin/swm-restore-test.sh  # prove the dump restores
```

Also keep a copy of `SECRET_KEY` in the church's password manager: it is the one thing that cannot be recreated.

## Checking it works

- `cat /opt/swm-backups/last-success` shows the time of the last good run. If it is more than a day old, read `/var/log/swm-backup.log`.
- Run `swm-restore-test.sh` once a month and after any Postgres upgrade. It restores the newest dump into a scratch database (`swm_restore_test`), compares row counts with the live database and drops the scratch copy. The live database is only read.

## Restoring for real

1. Stop the app so nothing writes: `cd /opt/swm && docker compose stop app worker beat transcriber`.
2. Take a dump of the current state first (even if it is broken): `swm-backup.sh`.
3. Restore the database (this replaces it):
   `docker exec -i swm-postgres pg_restore -U swm_user -d swm_db --clean --if-exists --no-owner < /opt/swm-backups/db/<file>.dump`
4. Copy files back: `rsync -a /opt/swm-backups/files/app_media/ <host folder mounted at /app/media>/` (and the same for the other folders).
5. Make sure `/opt/swm/.env` has the same `SECRET_KEY` as the backup's `env-*` copy.
6. Start the app: `docker compose up -d`, then open the site and the console and check Settings shows keys as saved.
