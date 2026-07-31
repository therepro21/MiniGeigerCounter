#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/minigeiger
DATA_DIR=/var/lib/minigeiger
SERVICE_USER=minigeiger

if [[ $EUID -ne 0 ]]; then echo "Bitte mit sudo ausführen."; exit 1; fi
if ! command -v apt-get >/dev/null; then echo "Raspberry Pi OS/Debian erwartet."; exit 1; fi

apt-get update
# USB audio devices sold as "mini sound cards" normally use the kernel driver
# snd-usb-audio.  These packages provide ALSA device discovery, mixer profiles
# and diagnostics; no vendor binary driver is installed.
apt-get install -y python3 python3-venv python3-pip portaudio19-dev libportaudio2 \
  libasound2 alsa-utils alsa-ucm-conf usbutils kmod
modprobe snd-usb-audio || true
install -d /etc/modules-load.d
printf '%s\n' 'snd-usb-audio' > /etc/modules-load.d/minigeiger-usb-audio.conf
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
install -d "$APP_DIR"
cp app.py requirements.txt "$APP_DIR/"
cp -r static "$APP_DIR/"
[[ -f "$DATA_DIR/config.json" ]] || install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 640 config.example.json "$DATA_DIR/config.json"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"
usermod -aG audio "$SERVICE_USER"
install -m 644 minigeiger.service /etc/systemd/system/minigeiger.service
systemctl daemon-reload
systemctl enable --now minigeiger.service
echo "Fertig. Dashboard: http://$(hostname -I | awk '{print $1}'):8734"
echo "Erkannte ALSA-Aufnahmegeräte:"
arecord -l || true
