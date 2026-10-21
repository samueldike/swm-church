#!/bin/sh
# Runs the official telegram-bot-api image with the App api_id / App api_hash an administrator
# saved in Admin -> Settings -> Audio distribution. The app writes them to $CONF (a volume only
# the app and this container share) and touches $CONF/stamp; this loop waits until they exist
# and restarts the server whenever they change. No keys live in compose files or .env.
CONF=/run/swm-telegram

while :; do
  if [ -s "$CONF/api_id" ] && [ -s "$CONF/api_hash" ]; then
    stamp=$(cat "$CONF/stamp" 2>/dev/null)
    echo "swm: starting telegram-bot-api with the keys saved in Settings"
    TELEGRAM_API_ID_FILE="$CONF/api_id" TELEGRAM_API_HASH_FILE="$CONF/api_hash" /docker-entrypoint.sh &
    pid=$!
    while kill -0 "$pid" 2>/dev/null && [ "$(cat "$CONF/stamp" 2>/dev/null)" = "$stamp" ]; do
      sleep 3
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "swm: keys changed in Settings, restarting"
      kill "$pid"; wait "$pid" 2>/dev/null
    else
      wait "$pid" 2>/dev/null
      echo "swm: telegram-bot-api stopped (exit $?), retrying in 10s"
      sleep 10
    fi
  else
    sleep 5
  fi
done
