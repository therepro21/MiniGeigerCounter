# MiniGeigerCounter

Lokaler Raspberry-Pi-Dienst für den **FTLab Smart Geiger Pro SGP001** am Mikrofoneingang einer USB-Soundkarte. Er zählt Audioimpulse, speichert aktuelle und historische Messwerte, stellt sie im mobilen Web-Dashboard dar und sendet sie per MQTT inklusive Home-Assistant-Discovery.

> Hinweis: Das SGP001 ist ein indikatives Messgerät. Der Dosiskonversionsfaktor ist je Aufbau zu kalibrieren; die Anzeige ist kein Ersatz für ein geeichtes Dosimeter.

## Eigenschaften

- Auswahl eines konkreten Audio-Eingangs im Webinterface (mehrere USB-Mikrofone möglich)
- Pulsdetektion mit Pegel, Holdoff und Schwellwert einstellbar
- 60-Sekunden-CPM, gleitender CPM und uSv/h (frei kalibrierbarer Faktor)
- SQLite-Verlauf (5-Minuten-Aggregate, 90 Tage) sowie JSON-API
- MQTT, retained Status und Home-Assistant MQTT Discovery
- eigenes responsives Dashboard, WebSocket-Liveupdates
- eigener Dienst auf Port `8734` (konfigurierbar), keine Docker-Abhängigkeit

## Installation

Auf Raspberry Pi OS 64-bit (Pi 3B+, Pi 4 oder Pi 5):

```bash
git clone https://github.com/DEIN-BENUTZER/MiniGeigerCounter.git
cd MiniGeigerCounter
sudo bash install.sh
```

Danach `http://PI-IP:8734` aufrufen, den Eingang wählen und in den Einstellungen die Impulsschwelle anhand der Pegelanzeige einstellen. Der Dienst läuft als eigener Systembenutzer `minigeiger` und startet automatisch.

## USB-Soundkarte wird nicht angezeigt

Der Installer installiert `alsa-utils`, die ALSA-Profile und lädt `snd-usb-audio` – den bereits im Raspberry-Pi-Kernel enthaltenen Treiber für die üblichen class-compliant USB-Soundkarten. Nach einer Neuinstallation oder nach Anstecken der Karte prüfe direkt auf dem Pi:

```bash
lsusb
arecord -l
arecord -L
sudo systemctl restart minigeiger
```

`arecord -l` muss die Karte als **card ... device ...** aufführen. Erst dann kann sie im Dashboard erscheinen. Wenn sie dort fehlt, ist kein zusätzliches Herstellerpaket die Lösung: anderes USB-Kabel/anderen Port testen, eine passive USB-Verlängerung vermeiden und bei Pi 3B+ oder mehreren USB-Geräten eine ausreichend versorgte Soundkarte bzw. einen aktiven Hub verwenden. Bei Karten mit Mikrofonbuchse muss der Stecker des SGP001 außerdem am **Mic-In** und nicht an einem reinen Kopfhörer-/Line-Out stecken.

## MQTT

Standardbasis: `minigeiger`. Beispiele:

| Topic | Payload |
|---|---|
| `minigeiger/radiation_usvh` | Zahl, z. B. `0.086` |
| `minigeiger/cpm` | Zahl |
| `minigeiger/count_total` | Zahl |
| `minigeiger/status` | `online` / `offline` |

Discovery wird nach `homeassistant/sensor/minigeigercounter_*` publiziert. MQTT kann auch vollständig deaktiviert werden.

## Wartung

```bash
sudo systemctl status minigeiger
sudo journalctl -u minigeiger -f
sudo systemctl restart minigeiger
```

Die Konfiguration und Daten liegen in `/var/lib/minigeiger/`. Im Dashboard lassen sich Konfiguration, Audioquelle und Kalibrierung ohne Neustart ändern.

## Kalibrierung

`uSv/h = CPM / counts_per_usvh`. Der Standardwert `11.26` ist bewusst nur ein Ausgangswert und darf nicht als Herstellerkalibrierung verstanden werden. Trage einen für deinen Sensor und Aufbau ermittelten Faktor ein. Die Firmware ignoriert Pulse innerhalb der Holdoff-Zeit, damit ein einzelner Klick nicht mehrfach gezählt wird.

## Entwicklung

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```
