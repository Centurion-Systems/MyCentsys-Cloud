# Changelog

## 1.0.1 (2026-11-13)

- **G-ULTRA and G-SPEAK ULTRA**: a device that is idle is now read every 5 minutes by default
  (was 10), and the interval under Configure can be set as low as 10 seconds (was 60). An ULTRA
  device does not report a change by itself, so a short interval is how a gate opened with a
  remote or a keypad shows up quickly. The follow-up every 2 seconds after an action sent from
  Home Assistant is unchanged. An install that has saved its options keeps the interval it saved.

## 1.0.0 (2026-11-11) — first release

MyCentsys Cloud brings Centurion Systems gate and garage door operators and G-ULTRA /
G-SPEAK ULTRA devices into Home Assistant through your MyCentsys account.

- Sign in with your country code, mobile number and a one-time PIN, then choose which devices
  to include: all, SMART operators only, ULTRA devices only, or your own selection. Use the
  number registered in the MyCentsys Remote app: a PIN is only sent to registered numbers. No
  shared credential is built into the integration.
- **SMART operators** (D-series sliders, VECTOR swings, SDO5 garage doors): a cover with open,
  close and stop, the app's status tiles (power supply, battery, health, tamper alarm), live
  position updates, and the operator's own answer when an action is refused.
- **Gates**: an icon that follows the position (closed, open, opening, closing), and separate
  **Open** and **Close** buttons with the open and closed gate icons.
- **Status icons** that follow the state: the cloud connection, battery, mains, operator status,
  Wi-Fi signal and tamper alarm of a SMART operator, and the connection and the armed or disarmed
  status of an ULTRA device.
- **G-ULTRA and G-SPEAK ULTRA**: gate and garage door covers with open, close and stop, live
  position from the position input, outputs as switches and buttons with the installer's names,
  inputs as status sensors with their state names, an alarm panel where an arm output exists,
  signal strength and the balance (call and SMS tokens on a token SIM, the network balance on any
  other) as sensors, and firmware, connection type, antenna, number and IMEI on the device page.
- **Shared gates**: gates shared with your number, with their remaining uses and expiry.
- Configurable under Configure: the devices, and how often ULTRA devices are read.
