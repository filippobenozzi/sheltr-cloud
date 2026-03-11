#!/bin/sh
set -eu

USERNAME="${MQTT_USERNAME:-filippo}"
PASSWORD="${MQTT_PASSWORD:-filippo1994}"
PASSFILE="/mosquitto/data/passwords.txt"
PASSDIR="/mosquitto/data"

if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "MQTT_USERNAME e MQTT_PASSWORD devono essere valorizzati." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
TMP_PASSFILE="$TMP_DIR/passwords.txt"
mkdir -p "$PASSDIR"
mosquitto_passwd -b -c "$TMP_PASSFILE" "$USERNAME" "$PASSWORD"
mv "$TMP_PASSFILE" "$PASSFILE"
chown mosquitto:mosquitto "$PASSDIR" "$PASSFILE"
chmod 640 "$PASSFILE"
rmdir "$TMP_DIR"

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
