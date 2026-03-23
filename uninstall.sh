#!/bin/sh
set -eu

APP_ROOT=${APP_ROOT:-/opt/vmquota}
BIN_DIR=${BIN_DIR:-/usr/local/bin}
CONFIG_DIR=${CONFIG_DIR:-/etc/vmquota}
STATE_DIR=${STATE_DIR:-/var/lib/vmquota}
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
PURGE_CONFIG=0
PURGE_STATE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --purge-config)
      PURGE_CONFIG=1
      ;;
    --purge-state)
      PURGE_STATE=1
      ;;
    --purge-all)
      PURGE_CONFIG=1
      PURGE_STATE=1
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  echo "uninstall.sh must run as root" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now vmquota-sync.timer >/dev/null 2>&1 || true
  systemctl stop vmquota-sync.service >/dev/null 2>&1 || true
  systemctl disable --now vmquota-api.service >/dev/null 2>&1 || true
fi

rm -f "$UNIT_DIR/vmquota-sync.service" "$UNIT_DIR/vmquota-sync.timer" "$UNIT_DIR/vmquota-api.service"
rm -f "$BIN_DIR/vmquota"
rm -rf "$APP_ROOT"

if [ "$PURGE_CONFIG" -eq 1 ]; then
  rm -rf "$CONFIG_DIR"
fi

if [ "$PURGE_STATE" -eq 1 ]; then
  rm -rf "$STATE_DIR"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
fi

echo "Uninstalled vmquota"
if [ "$PURGE_CONFIG" -eq 0 ]; then
  echo "Preserved config: $CONFIG_DIR"
fi
if [ "$PURGE_STATE" -eq 0 ]; then
  echo "Preserved state: $STATE_DIR"
fi
