#!/bin/sh
set -eu

APP_ROOT=${APP_ROOT:-/opt/vmquota}
BIN_DIR=${BIN_DIR:-/usr/local/bin}
CONFIG_DIR=${CONFIG_DIR:-/etc/vmquota}
STATE_DIR=${STATE_DIR:-/var/lib/vmquota}
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
NO_START=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-start)
      NO_START=1
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh must run as root" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("python3 >= 3.11 is required")
PY

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "$APP_ROOT" "$BIN_DIR" "$CONFIG_DIR" "$STATE_DIR" "$UNIT_DIR"
rm -rf "$APP_ROOT/src" "$APP_ROOT/docs" "$APP_ROOT/examples" "$APP_ROOT/guest"
mkdir -p "$APP_ROOT/src"
cp -R "$SCRIPT_DIR/src/vmquota" "$APP_ROOT/src/"
cp -R "$SCRIPT_DIR/docs" "$APP_ROOT/"
cp -R "$SCRIPT_DIR/examples" "$APP_ROOT/"
cp -R "$SCRIPT_DIR/guest" "$APP_ROOT/"
cp "$SCRIPT_DIR/README.md" "$APP_ROOT/README.md"

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
  cp "$SCRIPT_DIR/examples/config.toml" "$CONFIG_DIR/config.toml"
elif ! grep -q '^\[api\]' "$CONFIG_DIR/config.toml"; then
  cat >> "$CONFIG_DIR/config.toml" <<'EOF'

[api]
bind_host = "10.200.0.1"
bind_port = 9527
EOF
fi

cat > "$BIN_DIR/vmquota" <<'SH'
#!/bin/sh
set -eu
APP_ROOT=${APP_ROOT:-/opt/vmquota}
CONFIG_FILE=${VMQUOTA_CONFIG:-/etc/vmquota/config.toml}
PYTHONPATH="$APP_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m vmquota --config "$CONFIG_FILE" "$@"
SH
chmod 0755 "$BIN_DIR/vmquota"

cp "$SCRIPT_DIR/systemd/vmquota-sync.service" "$UNIT_DIR/vmquota-sync.service"
cp "$SCRIPT_DIR/systemd/vmquota-sync.timer" "$UNIT_DIR/vmquota-sync.timer"
cp "$SCRIPT_DIR/systemd/vmquota-api.service" "$UNIT_DIR/vmquota-api.service"

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl enable vmquota-sync.timer >/dev/null
  systemctl enable vmquota-api.service >/dev/null
  if [ "$NO_START" -eq 0 ]; then
    systemctl restart vmquota-sync.timer
    systemctl restart vmquota-api.service
  fi
fi

touch "$STATE_DIR/.keep"

echo "Installed vmquota"
echo "Config: $CONFIG_DIR/config.toml"
echo "State dir: $STATE_DIR"
