# MiniGeigerCounter v2.5

Lokaler, ressourcenschonender Raspberry-Pi-Dienst für Geigerzähler. Das Dashboard, MQTT und Home-Assistant-Discovery funktionieren ohne Cloud und teilen sich den Pi konfliktarm mit anderen Diensten.

Unterstützte Zählerprofile:

| Profil | Dateneingang | Standard-Kalibrierung |
|---|---|---|
| FTLab Smart Geiger Pro SGP001 | Mikrofoneingang einer USB-Soundkarte | vorhandene SGP001-Kalibrierung |
| RadiationD v1.1 (CAJOE) | GPIO-Impuls oder abgenommener Lautsprecherimpuls per Audio | abhängig vom verbauten Rohr |
| DFRobot Gravity Geiger Counter V1 / SEN0463 | digitaler D-Ausgang über GPIO | 153,8 CPM/(µSv/h) laut Hersteller |

> Sicherheit: Am Zählrohr und auf den Platinen liegt Hochspannung an. Nur die ausdrücklich beschriebenen Niederspannungs-Pins berühren. Ein Raspberry-Pi-GPIO ist **ausschließlich 3,3 V tolerant**. Niemals ein 5-V-Ausgang direkt mit GPIO verbinden.

## Installation

```bash
git clone https://github.com/therepro21/MiniGeigerCounter.git
cd MiniGeigerCounter
sudo bash install.sh
```

Danach `http://PI-IP:8734` öffnen. Rechts oben **Einstellungen** wählen, Zählermodell und Eingangsart einstellen, speichern. Der Dienst läuft als Benutzer `minigeiger`; die GPIO-Berechtigung und `gpiozero` werden durch den Installer eingerichtet.

## Verdrahtung

Die Beispiele verwenden **BCM GPIO 17**, physischer Pin 11. Jeder andere freie BCM-Pin 0–27 kann in den Einstellungen gewählt werden.

### 1. FTLab Smart Geiger Pro SGP001 - USB-Audio

```text
Smart Geiger Pro SGP001              Raspberry Pi
┌──────────────────────┐             ┌──────────────────────────┐
│  3,5-mm-Klinke OUT   ├────────────►│ USB-Soundkarte: MIC-IN   │
└──────────────────────┘             │ USB ───────────────────► │ USB
                                     └──────────────────────────┘
```

Den SGP001 am **Mikrofoneingang**, nicht am Kopfhörer-/Line-Out der Soundkarte anschließen. In den Einstellungen `FTLab Smart Geiger Pro SGP001 (Audio)` und danach den richtigen Audio-Eingang wählen. Bei mehreren Karten helfen `arecord -l` und die Pegelanzeige beim Zuordnen.

### 2A. RadiationD v1.1 (CAJOE) - digitaler GPIO-Impuls

```text
RadiationD P3*                    5-V → 3,3-V Pegelwandler           Raspberry Pi 40-Pin
┌───────────────┐                 ┌────────────────────────┐         ┌─────────────────┐
│ +5V ──────────┼─────────────────┼────────────────────────┼────────►│ Pin 2 oder 4: 5V│
│ GND ──────────┼─────────────────┼───────────────┬────────┼────────►│ Pin 6: GND      │
│ CPM / VIN OUT ┼────────────────►│ IN        OUT ├────────┼────────►│ Pin 11: BCM 17  │
└───────────────┘                 └────────────────────────┘         └─────────────────┘
```

`*` Je nach Boardrevision ist die Bezeichnung des Impulsausgangs abweichend; bei verbreiteten RadiationD-v1.1-Platinen ist `VIN` der Impulsausgang. Vor dem Anschluss die Platinenbeschriftung und das Schaltbild der eigenen Revision prüfen.

Als Pegelwandler eignet sich ein bidirektionaler Logic-Level-Converter. Alternativ darf ein korrekt berechneter Spannungsteiler verwendet werden, beispielsweise **10 kΩ vom RadiationD-Ausgang zum GPIO und 20 kΩ vom GPIO nach GND** (5 V werden auf etwa 3,3 V geteilt). In Einstellungen `RadiationD v1.1 (GPIO-Impuls)`, BCM 17 und `Puls ist aktiv LOW: Ja` wählen. Die übliche Schaltung zählt die fallende Flanke.

Ein separates, geregeltes 5-V-Netzteil darf statt Pi-Pin 2/4 verwendet werden. Dann muss die Masse (GND) mit dem Pi verbunden werden. Keine Versorgung an GPIO-Pins anschließen.

### 2B. RadiationD v1.1 - Lautsprecher-Signal per Audio

```text
RadiationD Mini-Lautsprecher            Koppel-/Dämpfglied             USB-Soundkarte
┌──────────────────────────┐            ┌────────────────────┐         ┌────────────────┐
│ SPK+ ─────────────────────┼───────────►│ 1 µF Kondensator   ├────────►│ MIC-IN Signal  │
│ SPK- / GND ───────────────┼───────────►│ 10 kΩ Serienwiderst.├───────►│ MIC-IN GND     │
└──────────────────────────┘            └────────────────────┘         └────────────────┘
```

Das Lautsprechersignal kann deutlich größer als ein Mikrofoneingang sein. Deshalb nur über ein Koppel-/Dämpfglied anschließen (kein direkter Anschluss) und mit hoher Impulsschwelle beginnen. In Einstellungen `RadiationD v1.1 (Lautsprecher-Audio)` sowie den betreffenden USB-Audioeingang wählen. Die Pegelanzeige dient zum sicheren Einstellen von Schwelle und Holdoff.

### 3. DFRobot Gravity Geiger Counter V1 / SEN0463 - GPIO

```text
DFRobot Gravity 3-polig                   Raspberry Pi 40-Pin
┌────────────────────────┐                ┌──────────────────┐
│ - / GND ───────────────┼───────────────►│ Pin 6: GND        │
│ + / VCC (3,3 V) ───────┼───────────────►│ Pin 1: 3,3 V      │
│ D / Signal ────────────┼───────────────►│ Pin 11: BCM 17    │
└────────────────────────┘                └──────────────────┘
```

Der DFRobot-Sensor arbeitet mit 3,3–5 V und zieht seinen digitalen Ausgang beim Impuls nach LOW. Für den direkten Pi-Anschluss daher mit **3,3 V** speisen. Bei Versorgung mit 5 V ist vor D ein 5-V→3,3-V-Pegelwandler nötig. In Einstellungen `DFRobot Gravity V1 / SEN0463 (GPIO-Impuls)`, BCM 17 und aktive LOW-Flanke wählen.

## Kalibrierung und Werte

`Aktuell CPS` zählt ausschließlich die Impulse der letzten Sekunde. CPM, Dosis, MQTT und Verlauf werden aus effizienten Zeitfenstern erzeugt; lange Historien bremsen die Erfassung nicht.

Die Umrechnung ist immer nur so gut wie Zählrohr, Geometrie und Kalibrierung. In **Einstellungen** kann ein Referenzgerät eingetragen werden: Referenz-CPS und zugehörige µSv/h ergeben automatisch den Faktor für CPM und CPS. Beim DFRobot-SEN0463 nennt der Hersteller 153,8 CPM/(µSv/h); RadiationD ist abhängig vom konkret verbauten Rohr und muss geprüft beziehungsweise kalibriert werden.

## MQTT und Home Assistant

Standardbasis: `minigeiger`.

| Topic | Inhalt |
|---|---|
| `minigeiger/radiation_usvh` | Dosisleistung in µSv/h |
| `minigeiger/cpm` | CPM, 1-Minuten-Fenster |
| `minigeiger/count_total` | interne Gesamtzählung für die Historie |
| `minigeiger/status` | `online` oder `offline` |

MQTT und automatische Home-Assistant-Discovery werden in Einstellungen aktiviert.

## Wartung und Diagnose

```bash
sudo systemctl status minigeiger --no-pager
sudo journalctl -u minigeiger -n 80 --no-pager
sudo systemctl restart minigeiger
arecord -l
```

Konfiguration und Datenbank liegen in `/var/lib/minigeiger/`; die Anwendung selbst in `/opt/minigeiger/`.

## Quellen und technische Hinweise

- [DFRobot SEN0463 Herstellerdokumentation](https://wiki.dfrobot.com/sen0463/): 3,3–5-V-Versorgung, D-Ausgang, aktive LOW-Pulse und 153,8 CPM/(µSv/h).
- [RadiationD-v1.1 Referenzprojekt](https://github.com/SensorsIot/Geiger-Counter-RadiationD-v1.1-CAJOE-): Schaltbild und Impulsausgang der Boardfamilie.
- [RadiationD-Pulszählbeispiel](https://devices.esphome.io/devices/geiger-counter/): fallende Flanke und Hinweise zum J305-Rohr.
