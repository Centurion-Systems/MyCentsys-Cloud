# Configuration

[Back to the README](../README.md)

Everything is set up in the Home Assistant interface; no YAML is needed.

## Options

**Settings -> Devices & Services -> MyCentsys Cloud -> Configure**:

- **Include**: all devices, SMART operators only, ULTRA devices only, or only the devices you
  choose. Sign-in starts on "Only the devices I choose". The first three follow your account, so a
  device linked to your number later is added on its own.
- **Devices to include**: the list used by "Only the devices I choose". Type part of a name to
  search it. Devices you leave out disappear from Home Assistant.
- **Idle ULTRA status interval** (default 600 seconds, 60 to 3600): how often each ULTRA
  device's inputs and gate position are read while nothing is happening. After any action the
  device is followed every 2 seconds for at least 30 seconds, and for as long as its position input
  reports movement (up to 3 minutes), so a slow door ends on its true position.

A device's own status (signal, tokens or network balance, supply voltage, connection, firmware) is read every 30
minutes, and only for devices with at least one of those sensors enabled.

## Live movement

SMART operators report movement the moment it happens, whoever caused it (remote, app or
schedule). This is always on. The integration only listens and never connects to an operator for
it, so the MyCentsys Remote app is not disturbed.

## Datacentre

Your account lives on one MyCentsys datacentre: **South Africa**, or **Australia and New Zealand**.
It follows from the country code you choose at sign-in, so there is nothing to set.

## Signing in again

The session lasts until it is withdrawn. If Home Assistant asks you to sign in again, a new
one-time PIN is sent to your number; enter it to reconnect. After a few wrong tries a PIN stops
working, so request a new one.
