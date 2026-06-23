# SHYS Remote

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Home Assistant integration to learn, store and replay remote control signals through the
built-in [`infrared`](https://www.home-assistant.io/integrations/infrared/)
integration.

- **Output signals** become `button` entities and send learned codes.
- **Input signals** become pulsing `binary_sensor` entities when a matching code
  is received.
- **Flipper-IRDB import** lets you search a bundled remote database and import
  all supported buttons during device setup.

## Requirements

- Home Assistant **2025.2** or newer
- An **ESPHome** device (or other hardware) that exposes **infrared receiver and emitter**
  entities in Home Assistant
- Home Assistant's built-in [**Infrared**](https://www.home-assistant.io/integrations/infrared/)
  integration — SHYS Remote builds on top of it and does not talk to GPIO hardware
  directly

### How the pieces connect

```text
IR hardware (LED + TSOP receiver)
        ↓
ESPHome: remote_receiver / remote_transmitter
        ↓
ESPHome: infrared (ir_rf_proxy)  →  HA entities (receiver + emitter)
        ↓
SHYS Remote  →  learn, send and match signals per logical device
```

When you add a device in SHYS Remote, you pick one **infrared receiver** and one
**infrared emitter** from Home Assistant. Those entities must exist before setup —
usually from an ESPHome node that uses the
[`ir_rf_proxy`](https://esphome.io/components/ir_rf_proxy/) platform.

## ESPHome reference setup

Below is a **minimal excerpt** of a working ESP32-S3 configuration (pins and names
are examples — adjust them for your board and wiring).

**Wiring (example):**

| Function | ESPHome component | Example pin |
| --- | --- | --- |
| IR receive | `remote_receiver` | GPIO4 (often `inverted: true` for TSOP modules) |
| IR send | `remote_transmitter` | GPIO6 |

**Relevant YAML:**

```yaml
# Receive raw IR timings from a demodulating receiver (e.g. 38 kHz)
remote_receiver:
  id: ir_rx
  pin:
    number: GPIO4
    inverted: true
  dump: raw

# Drive an IR LED (carrier generated in software)
remote_transmitter:
  id: ir_tx
  pin: GPIO6
  carrier_duty_percent: 50%
  non_blocking: true

# Expose hardware to Home Assistant as infrared entities (one instance each)
infrared:
  - platform: ir_rf_proxy
    name: IR Proxy Receiver
    receiver_frequency: 38kHz
    remote_receiver_id: ir_rx

  - platform: ir_rf_proxy
    name: IR Proxy Transmitter
    remote_transmitter_id: ir_tx
```

After flashing, add the ESPHome device to Home Assistant (**Settings → Devices &
services → ESPHome**). You should then see infrared receiver/emitter entities
(for example under the ESPHome device). Use those when configuring SHYS Remote.

The `api` action `send_raw_ir` in a full firmware is optional — useful for manual
tests from ESPHome/API. **SHYS Remote sends signals through Home Assistant's
Infrared integration**, not through that action.

**Documentation:**

- [ESPHome `remote_receiver`](https://esphome.io/components/remote_receiver/)
- [ESPHome `remote_transmitter`](https://esphome.io/components/remote_transmitter/)
- [ESPHome Infrared / `ir_rf_proxy`](https://esphome.io/components/ir_rf_proxy/)
- [Home Assistant Infrared](https://www.home-assistant.io/integrations/infrared/)

You also need the usual ESPHome building blocks (`esphome:`, `esp32:`, `api:`, `wifi:`,
`ota:`, …) — see the [ESPHome getting started guide](https://esphome.io/guides/getting_started_hassio/).

## Installation

### HACS (recommended after repository publish)

1. Add this repository as a [custom HACS repository](https://hacs.xyz/docs/faq/custom_repositories/).
2. Install **SHYS Remote**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/shys_remote` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Quick start

1. Open **Settings → Devices & services → Add integration**.
2. Search for **SHYS Remote** and complete the setup wizard.
3. Open the integration card and choose **Add device**.
4. Enter a device name, select your `infrared` receiver and transmitter, and pick
   how to populate signals (manual or IR database).

<p align="center">
  <img src="assets/add_device.png" alt="Add device — name, receiver, transmitter and signal source" width="480">
</p>

Each logical device appears as its own device in Home Assistant. Every learned
output signal is exposed as a button; every input signal as a binary sensor.

### Option A — Import from Flipper-IRDB

Choose **Import from IR database** when adding the device (or set the signal source
accordingly in the wizard). Search by brand, model or device type and optionally
filter by category.

<p align="center">
  <img src="assets/search_irdb.png" alt="Search the bundled Flipper-IRDB" width="480">
</p>

Pick a matching remote from the results — all supported buttons are imported in one step.

<p align="center">
  <img src="assets/choose_preset.png" alt="Choose a remote preset from search results" width="480">
</p>

<p align="center">
  <img src="assets/device_by_preset.png" alt="Imported device with button entities for each remote key" width="480">
</p>

### Option B — Learn signals manually

Leave the signal source on **manual**, then open **Manage device** on the integration
card and choose **Learn signal**.

<p align="center">
  <img src="assets/manage_device.png" alt="Manage device — edit, learn or delete signals" width="420">
  &nbsp;
  <img src="assets/learn_signal.png" alt="Learn signal — name, direction and timeout" width="420">
</p>

Output signals become pressable buttons; input signals become binary sensors that
pulse when a matching code is received.

<p align="center">
  <img src="assets/device_with_in_and_out.png" alt="Device with output button and input binary sensor" width="480">
</p>

## Integration options

Under **Configure** on the integration card you can tune matching and input behaviour
for all devices:

<p align="center">
  <img src="assets/integration_settings.png" alt="Global integration settings" width="480">
</p>

| Option | Description |
| --- | --- |
| Input pulse duration | How long input binary sensors stay `on` after a match |
| Signal match tolerance | Allowed timing deviation when matching received patterns |
| Input debounce | Minimum time between two triggers of the same input signal |

## Services

| Service | Description |
| --- | --- |
| `shys_remote.learn` | Learn a new signal on a device |
| `shys_remote.send` | Send a learned output signal |
| `shys_remote.delete` | Delete a learned signal and its entity |

The `device` parameter is the device **slug** shown in the subentry settings
(for example `soundbar_buro`).

Example:

```yaml
service: shys_remote.learn
data:
  device: soundbar_buro
  name: power
  direction: output
  timeout: 15
```

## Flipper-IRDB

The search index is shipped locally in `data/irdb_index.json`. Individual `.ir`
files are downloaded from GitHub only when you import a remote.

- Source: [Flipper-IRDB](https://github.com/Lucaslhm/Flipper-IRDB)
- License: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- Details: see [`data/IRDB_NOTICE.md`](custom_components/shys_remote/data/IRDB_NOTICE.md)

## Removal

1. Delete the **SHYS Remote** integration under **Settings → Devices & services**.
2. Remove `custom_components/shys_remote` from your configuration directory.
3. Restart Home Assistant.

Optional: delete `.storage/shys_remote` if you no longer need learned signals.

## License

Integration code: [MIT](LICENSE)

Flipper-IRDB data: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)

---

# SHYS Remote (Deutsch)

Home-Assistant-Integration zum Anlernen, Speichern und Senden von
Fernbedienungssignalen über die eingebaute `infrared`-Integration.

- **Output:** Buttons zum Senden
- **Input:** Binärsensoren bei erkanntem Signal
- **Flipper-IRDB:** Lokale Suche und Import beim Gerät anlegen

### Hardware (ESPHome)

SHYS Remote spricht nicht direkt mit GPIO — es nutzt die Home-Assistant-Integration
[**Infrared**](https://www.home-assistant.io/integrations/infrared/). Dafür brauchst
du ein Gerät (typisch **ESPHome**), das Empfänger und Sender als infrared-Entitäten
bereitstellt.

Kurz: `remote_receiver` + `remote_transmitter` in ESPHome, darüber je eine Instanz
[`infrared` / `ir_rf_proxy`](https://esphome.io/components/ir_rf_proxy/) — dann das
ESPHome-Gerät in HA einbinden. Beim Anlegen eines SHYS-Remote-Geräts wählst du
diese Receiver- und Transmitter-Entitäten aus.

Ausführliches Beispiel mit YAML und Links: Abschnitt **ESPHome reference setup** oben.

### Kurzstart

1. Integration **SHYS Remote** hinzufügen
2. Unter der Integration **Gerät hinzufügen** — Name, Receiver, Transmitter und
   Signalquelle wählen (siehe Screenshot oben)
3. **Flipper-IRDB:** Datenbank durchsuchen, Fernbedienung wählen → alle Tasten
   werden als Buttons angelegt
4. **Manuell:** Unter **Gerät verwalten → Signal anlernen** Output- oder
   Input-Signale aufzeichnen

Screenshots und Ablauf: Abschnitt **Quick start** oben (Oberfläche auf Deutsch).

Dokumentation in Home Assistant: Integrationskarte → **Dokumentation** (Link
aus `manifest.json`).
