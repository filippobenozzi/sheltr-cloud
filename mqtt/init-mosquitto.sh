#!/bin/sh
set -eu

USERNAME="${MQTT_USERNAME:-filippo}"
PASSWORD="${MQTT_PASSWORD:-filippo1994}"
PASSFILE="/mosquitto/config/passwords.txt"

if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "MQTT_USERNAME e MQTT_PASSWORD devono essere valorizzati." >&2
  exit 1
fi

TMP_PASSFILE="$(mktemp)"
mosquitto_passwd -b -c "$TMP_PASSFILE" "$USERNAME" "$PASSWORD"
mv "$TMP_PASSFILE" "$PASSFILE"
chmod 600 "$PASSFILE"

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
