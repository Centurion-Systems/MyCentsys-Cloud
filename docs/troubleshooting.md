# Troubleshooting

[Back to the README](../README.md)

- **No PIN arrives**: check that the number is the one registered in the MyCentsys Remote app and
  that the country code is right. Too many requests in a short time are refused for a while;
  the message says so.
- **No gates listed**: open the app with the same number. If the gate is not there, its admin has
  to add your number: as a remote user for a SMART operator, or as an access number for an ULTRA
  device. The gate appears in Home Assistant automatically within about a minute of being linked.
- **A device is missing after setup**: open **Configure** and check what is included. "All
  devices" follows your account; "Only the devices I choose" shows just the ones you ticked.
- **ULTRA position stays unknown**: the device has no Gate Status / Door Status input on IO 4.
  Control still works; the gate simply shows both buttons.
- **ULTRA devices missing**: the integration logs why. Open **Settings -> System -> Logs** and
  filter for `mycentsys_cloud`.
- **An action is refused**: the message is the operator's own answer, for example busy with
  another connection, offline, number not registered on the operator, time barred, or
  configuration changed. Try again once the operator is free.
- **A shared SMART gate cannot be opened**: shared SMART gates open over Bluetooth at the gate,
  so they are view only in Home Assistant.

## Debug logging

**Settings -> Devices & Services -> MyCentsys Cloud -> ⋮ -> Enable debug logging**, repeat the
problem, then disable it to download the log. Credentials and phone numbers are never written to
the log.

## Reporting a problem

Open an issue on the [issue tracker](https://github.com/Centurion-Systems/MyCentsys-Cloud/issues)
with your Home Assistant version, the integration version and, if you can, the debug log.
