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
apt-get install -y python3 python3-venv python3-pip python3-gpiozero python3-lgpio portaudio19-dev libportaudio2 \
  libasound2 alsa-utils alsa-ucm-conf usbutils kmod git sudo ca-certificates
modprobe snd-usb-audio || true
install -d /etc/modules-load.d
printf '%s\n' 'snd-usb-audio' > /etc/modules-load.d/minigeiger-usb-audio.conf
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
install -d "$APP_DIR"
cp app.py requirements.txt "$APP_DIR/"
cp VERSION "$APP_DIR/"
cp -r static "$APP_DIR/"
[[ -f "$DATA_DIR/config.json" ]] || install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 640 config.example.json "$DATA_DIR/config.json"
python3 -m venv --system-site-packages "$APP_DIR/.venv"
sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' "$APP_DIR/.venv/pyvenv.cfg"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"
getent group gpio >/dev/null || groupadd --system gpio
usermod -aG audio,gpio "$SERVICE_USER"
# Manual web updates are deliberately opt-in. The dashboard validates the
# user-selected update code; this narrowly scoped sudo rule can only run the
# root-owned updater below and cannot execute arbitrary commands.
SOURCE_DIR="$(pwd -P)"
install -d -m 755 /etc/minigeiger
printf 'REPO_DIR=%q\nBRANCH=main\n' "$SOURCE_DIR" > /etc/minigeiger/updater.conf
install -m 755 minigeiger-update /usr/local/sbin/minigeiger-update
printf '%s\n' 'minigeiger ALL=(root) NOPASSWD: /usr/local/sbin/minigeiger-update' > /etc/sudoers.d/minigeiger-update
chmod 440 /etc/sudoers.d/minigeiger-update
visudo -cf /etc/sudoers.d/minigeiger-update
# A former manual `systemctl mask minigeiger` leaves a /dev/null symlink at
# this path. Remove that mask before installing the actual unit file.
systemctl unmask minigeiger.service || true
install -m 644 minigeiger.service /etc/systemd/system/minigeiger.service
systemctl daemon-reload
# `enable --now` does not restart an already active service. A real restart is
# required after every installation so the running Python process loads app.py.
systemctl enable minigeiger.service
systemctl restart minigeiger.service
echo "Fertig. Dashboard: http://$(hostname -I | awk '{print $1}'):8734"
echo "Erkannte ALSA-Aufnahmegeräte:"
arecord -l || true
