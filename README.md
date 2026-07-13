# SHYS Remote

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Home Assistant integration to learn, store and replay remote control signals through the
built-in [`infrared`](https://www.home-assistant.io/integrations/infrared/) integration,
with optional support for
[`radio_frequency`](https://www.home-assistant.io/integrations/radio_frequency/)
transmitters (e.g. 433 MHz OOK devices) on top of the same learned-signal storage.

- **Output signals** become `button` entities and send learned codes.
- **Input signals** become pulsing `binary_sensor` entities when a matching code
  is received.
- **Both** creates a button and a binary sensor for the same signal — useful when
  you want to send and detect the same key.
- **Flipper-IRDB import** lets you search a bundled remote database and import
  supported signals during device setup.

### Signal direction

Every signal (learned manually or imported from IRDB) has a direction:

| Direction | Entities created |
| --- | --- |
| `output` | Send button |
| `input` | Binary sensor (pulses on match) |
| `both` | Send button **and** binary sensor |

## Requirements

- Home Assistant **2025.2** or newer
- An **ESPHome** device (or other hardware) that exposes an **infrared emitter**
  entity in Home Assistant
- An **infrared receiver** entity is optional, but required for learning signals and
  input/binary-sensor features — this applies to RF devices too, since learning always
  captures raw timings through an infrared receiver
- Home Assistant's built-in [**Infrared**](https://www.home-assistant.io/integrations/infrared/)
  integration — SHYS Remote builds on top of it and does not talk to GPIO hardware
  directly
- Optional, for RF devices: Home Assistant **2026.5** or newer for the built-in
  [**Radio Frequency**](https://www.home-assistant.io/integrations/radio_frequency/)
  integration, plus a compatible RF adapter (e.g. ESPHome or Broadlink) exposing a
  radio-frequency transmitter entity

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

When you add a device in SHYS Remote, you first choose the **signal medium** —
**infrared** or **radio frequency** — then pick one matching **transmitter** and
optionally an **infrared receiver** from Home Assistant. The transmitter must exist
before setup — for infrared, usually from an ESPHome node that uses the
[`ir_rf_proxy`](https://esphome.io/components/ir_rf_proxy/) platform; for RF, from
whatever adapter exposes an entity through Home Assistant's **Radio Frequency**
integration. The receiver is always an infrared receiver, even for RF devices, since
learning captures raw timings the same way regardless of medium.

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

## RF (radio-frequency) devices

RF support (e.g. 433 MHz OOK devices) reuses the same learn/store/send flow as infrared,
just through a different transmitter backend.

- Requires Home Assistant's built-in
  [**Radio Frequency**](https://www.home-assistant.io/integrations/radio_frequency/)
  integration (added in HA 2026.5) and a compatible RF adapter/proxy exposing a
  transmitter entity — see that integration's docs for supported hardware.
- When adding or editing a device, set **Signal medium** to **Radio frequency**, then
  pick the RF transmitter entity. The transmitter picker only lists entities Home
  Assistant currently knows as infrared or RF emitters.
- SHYS Remote's `shys_remote.send` service and buttons work the same for RF signals; only
  the underlying transmit call differs (`radio_frequency.async_send_command` instead of
  `infrared.async_send_command`).

RF and infrared devices coexist freely — the medium is a per-device setting, not a global
integration option.

Flipper-IRDB only contains infrared codes, so use **manual learning** (not IRDB import)
for RF devices.

### Learning RF signals (receiver workaround)

The **receiver** picker only ever lists infrared receiver entities, for RF devices too.
This is intentional, not a bug: as of Home Assistant Core 2026.7.2, the built-in
`radio_frequency` integration only creates *transmitter* entities. Home Assistant's
`esphome` integration filters incoming entities by their capability bits
(`homeassistant/components/esphome/radio_frequency.py`) and only keeps ones that
advertise transmit support — a receive-only RF proxy is silently dropped and never
becomes a Home Assistant entity, however correctly your ESPHome device announces it.
This is a current gap in Home Assistant Core, not something SHYS Remote can work around
in code, and may change once Home Assistant ships a native RF receiver entity.

Until then, learn RF signals through a receiver exposed via the **infrared** platform
instead — raw pulse/space timings are protocol-agnostic there, so an `ir_rf_proxy`
instance wired to your RF receiver hardware works for capturing RF codes even though
Home Assistant sees it as an infrared entity. For example, on a KinCony KC868-AG with
separate IR and RF receiver/transmitter pins:

```yaml
remote_receiver:
  - id: ir_rx
    pin:
      number: GPIO23
      inverted: true
    dump: raw
  - id: rf_rx
    pin: GPIO13
    dump: raw

remote_transmitter:
  - id: ir_tx
    pin: GPIO2
    carrier_duty_percent: 50%
    non_blocking: true
  - id: rf_tx
    pin: GPIO22
    carrier_duty_percent: 100%
    non_blocking: true

infrared:
  - platform: ir_rf_proxy
    name: IR Proxy Receiver
    receiver_frequency: 38kHz
    remote_receiver_id: ir_rx
  - platform: ir_rf_proxy
    name: IR Proxy Transmitter
    remote_transmitter_id: ir_tx
  - platform: ir_rf_proxy
    name: RF Learning Receiver
    remote_receiver_id: rf_rx

radio_frequency:
  - platform: ir_rf_proxy
    name: RF Proxy Transmitter
    frequency: 433.92MHz
    remote_transmitter_id: rf_tx
```

Notes on this config:

- `RF Learning Receiver` deliberately has no `receiver_frequency` — that field is IR
  demodulation metadata only (no hardware effect) and doesn't apply to raw RF capture.
- There is no native `radio_frequency:` receiver block for `rf_rx`. Home Assistant
  wouldn't create an entity for it anyway (see above), so it would just be dead
  configuration.
- In SHYS Remote, pick `RF Learning Receiver` as the **Receiver** and `RF Proxy
  Transmitter` as the **Transmitter** for an RF device — both are needed for `input`/
  `both` direction signals; output-only RF devices only need the transmitter.

This workaround is expected to stay necessary for a while: Home Assistant's `radio_frequency`
entity platform launched transmitter-only by design, with receiver entities explicitly
deferred to a future proposal (see the
[architecture discussion](https://github.com/home-assistant/architecture/discussions/1365)).
There is currently no version of Home Assistant Core where a native RF receiver entity
exists, so this isn't something a future SHYS Remote release can remove on its own.

### Learn and send use the exact same raw format

Learning and sending do **not** go through different data formats. Both the infrared and
radio-frequency backends store and replay the identical `list[int]` mark/space microsecond
sequence Home Assistant hands back from `InfraredReceivedSignal.timings` — SHYS Remote never
decodes, re-encodes or otherwise transforms it (see `manager.build_command()` and
`signal_transport.build_rf_command()`). No protocol (rc_switch, Intertechno, NEC, …) is ever
assumed; this is a pure record-and-replay design and works with any ESPHome-compatible
transmitter/receiver pair. If a learned RF signal doesn't work, the raw capture itself is
the first thing to check — not this integration's plumbing.

### If a learned RF signal doesn't control the device

Since learning captures whatever the receiver hardware reports, byte for byte, a bad capture
is stored and replayed just as faithfully as a good one. In practice, on cheap 433 MHz OOK
receiver modules:

- SHYS Remote now requires **two consecutive captures that agree** (within the integration's
  configured **Signal match tolerance**) before it stores an RF signal — press the remote
  button twice in a row when learning. This catches the common failure mode where AGC noise
  or the receiver's idle/gap threshold cuts each raw dump at a different point, so every
  press looks like a different signal. If learning keeps failing with "captures did not
  match", the receiver itself is too noisy to learn from reliably as configured — see below.
- Move the receiver antenna away from the ESP32/WiFi/USB noise sources, and closer to
  the remote during learning.
- On ESP32, a dedicated receiver chip (e.g. **CC1101**) generally decodes far more
  consistently than a bare OOK/ASK receiver module (e.g. FS1000A/XY-MK-5V-class boards),
  because it does its own signal conditioning instead of relying on the ESP32 to sample a
  noisy analog line.
- Check the `remote_receiver`'s `filter`/`idle` timing in your ESPHome YAML — too short an
  `idle` cuts a real code into multiple fragments; too long merges unrelated noise into one
  capture.
- On the transmit side, make sure the RF `remote_transmitter` has `carrier_duty_percent: 100%`
  (see the example above) — RF hardware does its own carrier generation, and any other duty
  cycle tells the ESP32 to additionally modulate the line in software, which corrupts the
  raw envelope for genuinely fixed-code OOK receivers.
- If receiving still won't decode consistently even on a CC1101, but sending confirms RF
  energy is emitted, treat it as a receiver/antenna/firmware issue to resolve on the ESPHome
  side rather than something to work around in SHYS Remote — this integration only stores
  and replays whatever timings the receiver reports.

### Using other RF hardware (e.g. CC1101)

SHYS Remote has no vendor- or chip-specific code anywhere — it only ever talks to standard
Home Assistant `infrared`/`radio_frequency` entities, whatever integration created them. Any
ESPHome device (or Broadlink, or any other integration implementing those platforms) works
the moment it exposes such an entity; nothing needs adjusting in SHYS Remote itself. If a
device's entities don't show up in the transmitter/receiver picker, that always means Home
Assistant never created an entity for it — check the device's own ESPHome YAML and online
status first. The **Add device** step now shows a hint directly in Home Assistant when no
transmitter entities are found at all, pointing back here.

A common cause with **CC1101** boards specifically: unlike a bare FS1000A/XY-MK-5V module
that just needs a GPIO pin, the CC1101 is a real SPI radio and needs to be told when to
switch between transmit and receive mode. Wiring it under the wrong ESPHome platform (
`infrared:` instead of `radio_frequency:`), or omitting the mode-switch callbacks, results
in an ESPHome device that looks fine in the ESPHome logs but never creates a usable entity
in Home Assistant — indistinguishable, from SHYS Remote's side, from a device that was never
flashed. This requires **ESPHome 2026.5.0 or newer** (CC1101 support in the `radio_frequency:`
platform). Example:

```yaml
spi:
  clk_pin: GPIO18
  mosi_pin: GPIO23
  miso_pin: GPIO19

cc1101:
  id: cc1101_radio
  cs_pin: GPIO5
  gdo0_pin: GPIO27
  gdo2_pin: GPIO26
  frequency: 433.92MHz
  modulation_type: ASK_OOK

remote_transmitter:
  id: rf_tx
  pin: GPIO27
  carrier_duty_percent: 100%
  on_transmit:
    then:
      - cc1101.begin_tx: cc1101_radio
  on_complete:
    then:
      - cc1101.begin_rx: cc1101_radio

remote_receiver:
  id: rf_rx
  pin: GPIO26
  dump: raw

infrared:
  - platform: ir_rf_proxy
    name: RF Learning Receiver
    remote_receiver_id: rf_rx

radio_frequency:
  - platform: ir_rf_proxy
    name: RF Proxy Transmitter
    frequency: 433.92MHz
    remote_transmitter_id: rf_tx
```

This mirrors the KC868 example above: the receiver is still exposed through the `infrared:`
platform (the learning workaround explained earlier applies to any RF hardware, not just
FS1000A-style modules), while the transmitter goes through the native `radio_frequency:`
platform. The `on_transmit`/`on_complete` hooks are what actually switch the CC1101 between
its TX and RX radio states — without them the chip stays in whichever mode it last was and
transmission silently does nothing. See ESPHome's
[`cc1101`](https://esphome.io/components/cc1101/) docs for the full option list (dual-pin
vs. single-pin wiring, output power, symbol rate) and adjust pins for your board.

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
4. Enter a device name, choose the **signal medium** (infrared or radio frequency),
   select your transmitter (receiver optional), and pick how to populate signals
   (manual, or Flipper-IRDB import — the bundled database only contains infrared
   codes, so use manual learning for RF devices).

<p align="center">
  <img src="assets/add_device.png" alt="Add device — name, receiver, transmitter and signal source" width="480">
</p>

Each logical device appears as its own device in Home Assistant. Depending on the
chosen direction, signals become buttons, binary sensors, or both.

### Option A — Import from Flipper-IRDB

Choose **Import from IR database** when adding the device. Search by brand, model or
device type and optionally filter by category.

<p align="center">
  <img src="assets/search_irdb.png" alt="Search the bundled Flipper-IRDB" width="480">
</p>

Pick a matching remote from the results. On the next step, choose the signal
direction (`output`, `input` or `both`) and confirm the import.

<p align="center">
  <img src="assets/choose_preset.png" alt="Choose a remote preset from search results" width="480">
</p>

<p align="center">
  <img src="assets/device_by_preset.png" alt="Imported device with entities for each remote key" width="480">
</p>

By default, IRDB imports use **output** (send buttons). Choose **both** if you also
want binary sensors for automations when the same keys are received.
If no receiver is configured on the device, only **output** is available.

### Option B — Learn signals manually

Leave the signal source on **manual**, then open **Manage device** on the integration
card and choose **Learn signal**.

<p align="center">
  <img src="assets/manage_device.png" alt="Manage device — edit, learn or delete signals" width="420">
  &nbsp;
  <img src="assets/learn_signal.png" alt="Learn signal — name, direction and timeout" width="420">
</p>

Select **output**, **input** or **both**, submit the form, then press the button on
the physical remote within the timeout.
Learning is only available when a receiver is configured on the device.

For **RF devices**, learning is two form steps: submitting the name/direction/timeout
form waits for a first capture, and on success shows a second, one-click **Confirm
signal** form — submitting that waits for a second capture. Both captures must agree
(within the integration's **Signal match tolerance** option) before the signal is
stored — see
["If a learned RF signal doesn't control the device"](#if-a-learned-rf-signal-doesnt-control-the-device)
for what a persistent mismatch usually means. IR devices are unaffected by this and only
need a single capture, as before.

<p align="center">
  <img src="assets/device_with_in_and_out.png" alt="Device with output button and input binary sensor" width="480">
</p>

## Device options

When adding or editing a device you can configure how output signals are sent:

| Option | Description |
| --- | --- |
| Send repetitions | How many times each signal is sent in a row (default: 1) |
| Delay between repetitions | Wait time between consecutive sends in milliseconds (default: 45 ms) |

Some devices (for example Sony TVs with SIRC) need multiple repeats to react reliably.

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
| `shys_remote.learn` | Learn a new signal on a device (requires receiver) |
| `shys_remote.send` | Send a learned output signal |
| `shys_remote.delete` | Delete a learned signal and its entity |

The `device` parameter is the device **slug** shown in the subentry settings
(for example `soundbar_buro`).

The `direction` parameter for `learn` accepts `output`, `input` or `both`.

Example — learn an output signal:

```yaml
service: shys_remote.learn
data:
  device: soundbar_buro
  name: power
  direction: output
  timeout: 15
```

Example — learn a signal for sending and receiving:

```yaml
service: shys_remote.learn
data:
  device: soundbar_buro
  name: power
  direction: both
  timeout: 15
```

## Flipper-IRDB

The search index is shipped locally in `data/irdb_index.json`. Individual `.ir`
files are downloaded from GitHub only when you import a remote.

- Source: [Flipper-IRDB](https://github.com/Lucaslhm/Flipper-IRDB)
- License: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- Details: see [`data/IRDB_NOTICE.md`](data/IRDB_NOTICE.md)

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
Fernbedienungssignalen über die eingebaute `infrared`-Integration, optional auch über
[`radio_frequency`](https://www.home-assistant.io/integrations/radio_frequency/)
(z. B. 433-MHz-OOK-Geräte) auf Basis derselben Signal-Speicherung.

- **Output:** Buttons zum Senden
- **Input:** Binärsensoren bei erkanntem Signal
- **Beides:** Button und Binärsensor für dasselbe Signal
- **Flipper-IRDB:** Lokale Suche und Import beim Gerät anlegen (nur Infrarot-Codes)

### Signalrichtung

| Richtung | Angelegte Entitäten |
| --- | --- |
| `output` | Sende-Button |
| `input` | Binärsensor (kurzer Impuls bei Treffer) |
| `both` | Sende-Button **und** Binärsensor |

### Hardware (ESPHome)

SHYS Remote spricht nicht direkt mit GPIO — es nutzt die Home-Assistant-Integration
[**Infrared**](https://www.home-assistant.io/integrations/infrared/). Dafür brauchst
du ein Gerät (typisch **ESPHome**), das mindestens einen Sender als infrared-Entität
bereitstellt. Ein Empfänger ist optional, aber für Input/Binärsensoren und Anlernen nötig.

Kurz: `remote_receiver` + `remote_transmitter` in ESPHome, darüber je eine Instanz
[`infrared` / `ir_rf_proxy`](https://esphome.io/components/ir_rf_proxy/) — dann das
ESPHome-Gerät in HA einbinden. Beim Anlegen eines SHYS-Remote-Geräts wählst du zuerst
das **Signal-Medium** (Infrarot oder Funk), dann die passende Transmitter-Entität und
optional eine Empfänger-Entität aus.

Ausführliches Beispiel mit YAML und Links: Abschnitt **ESPHome reference setup** oben.

### Funk (RF)

Für RF-Geräte (z. B. 433-MHz-OOK) brauchst du Home Assistant **2026.5+** mit der
eingebauten [**Radio Frequency**](https://www.home-assistant.io/integrations/radio_frequency/)
Integration sowie einen kompatiblen RF-Adapter, der eine Transmitter-Entität
bereitstellt. Da Flipper-IRDB nur Infrarot-Codes enthält, lerne RF-Signale manuell an
statt sie zu importieren.

Der **Empfänger** bleibt auch bei RF-Geräten ein Infrared-Empfänger — das ist bewusst so,
kein Bug: Home Assistants `radio_frequency`-Integration erzeugt (Stand HA Core 2026.7.2)
ausschließlich Transmitter-Entities. Reine RF-Empfänger werden von der
`esphome`-Integration anhand des fehlenden `TRANSMITTER`-Capability-Bits
herausgefiltert und erscheinen dadurch nie als Home-Assistant-Entity — unabhängig davon,
wie korrekt dein ESPHome-Gerät sie meldet. Bis Home Assistant das nativ unterstützt,
lernst du RF-Signale stattdessen über eine zusätzlich als `infrared` eingebundene
Proxy-Receiver-Entity an, da rohe Puls-/Pause-Timings dort protokollunabhängig sind.
Konkretes ESPHome-Beispiel (KinCony KC868-AG) und Details: Abschnitt **Learning RF
signals (receiver workaround)** oben.

### Geräteoptionen

Beim Anlegen oder Bearbeiten eines Geräts kannst du das Sendeverhalten festlegen:

| Option | Beschreibung |
| --- | --- |
| Sendewiederholungen | Wie oft jedes Signal hintereinander gesendet wird (Standard: 1) |
| Pause zwischen Wiederholungen | Wartezeit zwischen den Sendungen in Millisekunden (Standard: 45 ms) |

Manche Geräte (z. B. Sony-TVs mit SIRC) reagieren erst zuverlässig bei mehreren Wiederholungen.

### Kurzstart

1. Integration **SHYS Remote** hinzufügen
2. Unter der Integration **Gerät hinzufügen** — Name, Signal-Medium (Infrarot/Funk),
   optional Empfänger, Transmitter und Signalquelle wählen (siehe Screenshot oben)
3. **Flipper-IRDB:** Datenbank durchsuchen, Fernbedienung wählen, im
   Bestätigungsschritt die Richtung festlegen (`output`, `input` oder `both`) →
   Signale werden importiert (ohne Empfänger nur `output`)
4. **Manuell:** Unter **Gerät verwalten → Signal anlernen** Richtung wählen
   (Senden, Empfangen oder Beides), Formular absenden und Taste auf der
   Fernbedienung drücken (nur verfügbar, wenn ein Empfänger konfiguriert ist)

Screenshots und Ablauf: Abschnitt **Quick start** oben (Oberfläche auf Deutsch).

Dokumentation in Home Assistant: Integrationskarte → **Dokumentation** (Link
aus `manifest.json`).
