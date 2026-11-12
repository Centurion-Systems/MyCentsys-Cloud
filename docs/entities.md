# Entities

[Back to the README](../README.md)

## SMART operators

Each operator is a **gate** (or **garage door**) with open, close and stop. A gate's icon follows
its position: the closed gate, the open gate, and a gate with an arrow while it is opening or
closing. A garage door shows its open door while it is moving. An ULTRA gate with a position input
does the same.

It has the same status row as the app, read from the operator itself:

- **Operator status**
- **Battery** (V)
- **Mains**
- **Health** (Good / Attention). The faults that set it are listed in its `conditions` attribute,
  and every other state the operator reports (modes, power states, relayed siren flags) in
  `notices`. Beam states such as made, broken, sleeping or disabled never raise it; a beam that
  is not connected or is miswired does.
- **Tamper alarm**
- **Cloud connection**, **Last contact** and **Signal strength** (Wi-Fi)

All of these are ordinary sensors; a SMART operator has no diagnostic section.

Their icons follow the state: the open or closed gate (or garage door) for the operator status,
with an arrow while it is partly open or moving; a low, faulty or missing battery; the plug struck
through while the mains is off; the shield struck through while the tamper alarm is disarmed; a
globe for the cloud connection, struck through while there is none; and Wi-Fi bars for the signal
strength.

Controls:

- Gates: **Open** and **Close** buttons with the open and closed gate icons. A gate has one
  trigger for both directions, so Open does nothing when the gate is already open or opening, and
  Close does nothing when it is already closed or closing.
- Gates: **Pedestrian**, **Cancel auto-close**, **Cancel PIRAC** and **Cancel auto-close and PIRAC**
  buttons, and the **Holiday lock** and **Keep open** switches.
- Garage doors: **Override auto-close**. A garage door opens and closes with its dedicated open
  and close actions, so the direction is always the one asked for.
- Sliders and garage doors: the **Tamper alarm armed** switch and the **Clear alarms** button.

## G-ULTRA and G-SPEAK ULTRA devices

An ULTRA device is a general-purpose GSM input/output device, so the integration reads what
it is wired to from the names the installer configured, and never assumes it is a gate.

| Device is wired to | How it is recognised | Top-level entity |
| --- | --- | --- |
| Gate | A **Gate Status** input on IO 4, or an output named Trigger / TRG / Gate | Gate |
| Garage door | A **Door Status** or **Garage Status** input on IO 4, or an output named Garage / GRG / GDO / Door | Garage door |
| Alarm | An **Arm** two-state output (with Armed / Disarmed state names); optional Alarm / Siren / Panic input | Alarm panel (arm away, disarm, triggered) |
| Electric fence | Names containing Fence / Energiser | None: the energiser is a switch, fence inputs are status sensors |
| Anything else | None of the above | None |

Whatever the type, **every input and output appears under its configured name**:

- a two-state output is a **switch**; its configured state name, for example "Armed", is in the
  `state_name` attribute;
- a momentary output is a **button**;
- every input is a **status sensor** whose states are the installer's names, for example
  "Full" / "Low" or "Running" / "Stopped".

A plain on/off binary sensor per input exists too, disabled by default. Use it, or the status
sensor's state names, in your own automations when the device is not one of the types above.

A gate or garage device has open, close and stop. All three pulse the same output, as the button
on a remote does. With a position input the device reports `opening`, `open`, `closing` and
`closed` live, is followed closely after each action, and stop is only sent while it is moving.
A gate also has **Open** and **Close** buttons with the open and closed gate icons, which work
the same way as on a SMART gate.
The status input decides which controls are shown: a garage status gives the garage door controls,
whatever the outputs are called. The device model shows the device type and what it is wired to,
for example "G-ULTRA 4G, Electric fence".

On a device wired to an alarm, an input or output that says whether the alarm is armed (a state
named Armed or Disarmed, or an IO named Arm) shows the shield, struck through while disarmed.

Sensors: **Signal strength** (0 to 5, shown with signal bars) and the device's balance. A device
on a token SIM shows **Tokens left (calls)** and **Tokens left (SMS)**. A device on any other SIM
shows **Network balance**, which is the network's own answer to the last balance request. Which
of the two a device shows follows the SIM type set up on the device, never both.
Diagnostics: **Supply voltage** and **Connection**. The device page shows the
rest under Device info: the firmware version and the device's number, its antenna and connection
type, and its IMEI in the serial number row.

Under Configuration are two buttons. **Refresh tokens** (on a token SIM) or **Refresh network
balance** (on any other SIM) asks the device for its balance; the answer is then looked for every
5 seconds for up to 30 seconds. **Refresh configuration** re-reads a device's names after the
installer changes them.

## Shared gates

Gates shared with your number (Access Sharing) appear with their **remaining uses** and **expiry**.
A shared ULTRA gate is controlled like any other. A shared SMART gate is view only: the app opens
it over Bluetooth at the gate, so there is no remote path, and Home Assistant shows a repair notice
explaining this.

## Running alongside the MyCentsys Remote app

Home Assistant and the app share one account, so the integration takes care not to get in the
app's way:

- It never registers for push notifications and never changes the app's registration, so the app
  keeps receiving its notifications.
- It uses its own connection, so the app's live connection is never taken over, and it lets go of
  an operator as soon as an action is answered.
- Live status only listens to what operators report; it never connects to an operator itself.
- When an operator is busy with the app or an installer tool, the action is refused with that
  reason rather than interrupting it.
- Reads are paced: see [Configuration](configuration.md).
