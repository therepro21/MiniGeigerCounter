# MiniGeigerCounter v3.1.2

[Deutsch](README.md) | [English](README.en.md)

MiniGeigerCounter is a local, resource-conscious Raspberry Pi service for Geiger counters. The dashboard, MQTT and Home Assistant discovery work without cloud services and can share a Pi with other applications.

![MiniGeigerCounter dashboard – example without a connected Geiger counter](docs/images/dashboard-no-geiger-device.png)

> Example dashboard without a connected Geiger counter. Shown measurement values are preview data only.

## Installation

```bash
git clone https://github.com/therepro21/MiniGeigerCounter.git
cd MiniGeigerCounter
sudo bash install.sh
```

Open `http://PI-IP:8734` afterwards (**web interface port: 8734**). Select **Einstellungen** in the upper right, choose the counter model and input, then save. The service runs as user `minigeiger`; the installer configures the required GPIO permissions and `gpiozero`.

MQTT uses port **1883** by default. Its standard base topic is `minigeiger` and the Home Assistant discovery prefix is `homeassistant`.

## Supported counters

| Profile | Input | Default calibration |
|---|---|---|
| FTLab Smart Geiger Pro SGP001 | microphone input of a USB sound card | existing SGP001 project calibration |
| RadiationD v1.1 (CAJOE) | GPIO pulse or speaker pulse captured by audio input | depends on the installed tube |
| DFRobot Gravity Geiger Counter V1 / SEN0463 | digital D output through GPIO | 153.8 CPM/(µSv/h), manufacturer value |

> Safety: The tube and counter boards use high voltage. Touch only the explicitly documented low-voltage pins. Raspberry Pi GPIO pins are **3.3 V only**. Never connect a 5 V output directly to a GPIO pin.

## Connections

The German documentation contains detailed ASCII wiring diagrams for all supported paths, including the RadiationD level shifter and power options: [German wiring guide](README.md#verdrahtung).

Quick reference: the examples use **BCM GPIO 17** (physical pin 11). GPIO 17, pin 1 (3.3 V), pins 2/4 (5 V) and pin 6 (GND) have the same physical-header positions on Raspberry Pi 3B+, 4 and 5.

### SGP001 through USB audio

```text
Smart Geiger Pro SGP001                  Raspberry Pi
3.5 mm output ────────────────────────► USB sound card MIC-IN
USB sound card USB ───────────────────► Raspberry Pi USB port
```

Use the microphone input, not headphone/line output. Choose `FTLab Smart Geiger Pro SGP001 (Audio)` and the correct audio input in the settings. `arecord -l` and the level meter help identify the right card when several devices are connected.

## Calibration

`Aktuell CPS`/current CPS uses only the pulses of the last second. CPM, dose, MQTT and history use efficient time windows. Pulse and level data is held in RAM; SQLite history and modified configuration are written in batches no more than once per minute.

Use **Einstellungen → Referenzgeiger kalibrieren** to enter a known reference CPS and its matching µSv/h value. The application calculates and applies both CPM and CPS conversion factors. Tube presets are only useful starting points; they do not replace a reference measurement or professional calibration.

## MQTT and Home Assistant

Configure these settings in **Einstellungen** to match a Mosquitto broker, such as a shared PV Optimizer / Home Assistant installation:

| Setting | Example / purpose |
|---|---|
| MQTT Host / Port | `192.168.0.31` / `1883` (local Mosquitto) |
| MQTT username / password | dedicated Mosquitto user; leaving the password empty retains the saved password |
| MQTT Topic | `minigeiger`; must be unique in the broker |
| Home Assistant discovery prefix | normally `homeassistant` |
| Home Assistant discovery | automatically creates the entities in Home Assistant |

| Topic | Content |
|---|---|
| `minigeiger/radiation_usvh` | dose rate in µSv/h |
| `minigeiger/cpm` | CPM, one-minute window |
| `minigeiger/cps` | CPS, pulses in the exact last second |
| `minigeiger/count_total` | internal total for history |
| `minigeiger/status` | `online` or `offline` |

All values and connection state are retained. With discovery enabled, dose, CPM, CPS and connectivity appear automatically as MiniGeigerCounter entities in Home Assistant.

## Updates from the web interface

Under **Einstellungen → Programm-Update**, **Update prüfen** checks the published GitHub version. **Update installieren** requires a personal update code (at least 16 characters), configured and saved in the settings first. The code is not shown again after saving.

The updater only performs a fast-forward pull in the repository used during installation, runs the installer and restarts the service. It will not overwrite local uncommitted changes. Failure details are written to `/var/log/minigeiger-update.log`.

After upgrading to v3.1, run the installer once manually to install the protected updater:

```bash
cd ~/MiniGeigerCounter
git pull origin main
sudo bash install.sh
```

Automatic updates at boot are intentionally disabled so an unavailable network, GitHub outage or problematic release cannot prevent normal measurement operation.

## Maintenance and diagnostics

```bash
sudo systemctl status minigeiger --no-pager
sudo journalctl -u minigeiger -n 80 --no-pager
sudo systemctl restart minigeiger
arecord -l
```

Configuration and the SQLite database are stored in `/var/lib/minigeiger/`; the application itself is in `/opt/minigeiger/`.

## Sources

- [DFRobot SEN0463 documentation](https://wiki.dfrobot.com/sen0463/)
- [RadiationD v1.1 reference project](https://github.com/SensorsIot/Geiger-Counter-RadiationD-v1.1-CAJOE-)
- [RadiationD pulse-counting example](https://devices.esphome.io/devices/geiger-counter/)
