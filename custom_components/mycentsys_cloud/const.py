"""Names, stored keys and timings of the MyCentsys Cloud integration.

Keys that Home Assistant already defines (country, name, email, region, token,
devices) come from ``homeassistant.const``; only the ones particular to a
MyCentsys account are declared here. The timings are grouped by the part of
the integration they pace, so a cadence can be tuned in one place.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "mycentsys_cloud"
MANUFACTURER: Final = "Centurion Systems"
# How an account is listed under Devices & Services, ahead of its number.
ENTRY_TITLE_PREFIX: Final = "MyCentsys Devices: "
# What came ahead of the number in builds before this one; such entries are renamed.
EARLIER_TITLE_PREFIXES: Final = ("", "MyCentsys Device - ")
PLATFORMS: Final = ("alarm_control_panel", "binary_sensor", "button", "cover", "sensor", "switch")

# --- what a config entry stores about the account ----------------------------------------------
CONF_ACCOUNT_NUMBER: Final = "account_number"  # E.164, with the leading plus
CONF_PIN_DELIVERY: Final = "pin_delivery"  # one of the PIN_BY_* values below
# Where builds before this one kept each operator's product code: in the entry itself. It is
# read once, moved to the integration's own storage (see ``learned.py``) and taken out.
EARLIER_OPERATOR_CODES: Final = "operator_codes"

# The values the gateway accepts for the PIN delivery channel.
PIN_BY_WHATSAPP: Final = 1
PIN_BY_SMS: Final = 2
DEFAULT_PIN_DELIVERY: Final = PIN_BY_WHATSAPP

# --- options (Configure dialog; saving reloads the entry) ---------------------------------------
# Which devices Home Assistant includes. A scope other than "chosen" follows the
# account, so a device linked later arrives on its own; "chosen" uses the fleet
# keys in CONF_DEVICES ("<serial>", "ultra-<id>" or "access-<guid>").
CONF_DEVICE_SCOPE: Final = "device_scope"
SCOPE_ALL: Final = "all"
SCOPE_SMART: Final = "smart"
SCOPE_ULTRA: Final = "ultra"
SCOPE_CHOSEN: Final = "chosen"
DEVICE_SCOPES: Final = (SCOPE_ALL, SCOPE_SMART, SCOPE_ULTRA, SCOPE_CHOSEN)
# What the device step of sign-in has selected when it opens.
SIGN_IN_DEVICE_SCOPE: Final = SCOPE_CHOSEN
CONF_ULTRA_POLL_INTERVAL: Final = "ultra_poll_interval"  # seconds between reads of an idle module
# An ULTRA does not announce a change by itself: something done at the device (a remote, a
# keypad) only shows at the next read. Five minutes suits most; an owner who wants such a change
# to show within seconds can go down to ten, at the price of that many more requests all day.
DEFAULT_ULTRA_POLL_INTERVAL: Final = 300
MIN_ULTRA_POLL_INTERVAL: Final = 10
MAX_ULTRA_POLL_INTERVAL: Final = 3600

# --- polling (seconds) --------------------------------------------------------------------------
CLOUD_POLL_SECONDS: Final = 60  # listings and cloud snapshots of SMART operators
# Shares are granted and withdrawn by people, so a few minutes between looks is enough. Using
# one from here reads them again at once, so its remaining uses do not wait for this.
GRANTS_POLL_SECONDS: Final = 250
MODULE_DIAGNOSTICS_SECONDS: Final = 1800  # read only while a diagnostic sensor is enabled

# --- following an ULTRA module after an action ----------------------------------------------------
MODULE_BURST_STEP_SECONDS: Final = 2  # gap between reads
MODULE_BURST_SECONDS: Final = 30  # shortest burst
MODULE_BURST_MAX_SECONDS: Final = 180  # longest burst, however long the input reports movement
MODULE_BURST_SETTLE_READS: Final = 2  # extra reads once the position is steady

# --- balance -------------------------------------------------------------------------------------
# The module fetches its balance and reports back in its own time, so the
# diagnostics are read again every few seconds for half a minute after the
# request, or until the answer is in.
BALANCE_FOLLOW_UP_SECONDS: Final = 5
BALANCE_FOLLOW_UP_READS: Final = 6

# --- SMART operators -----------------------------------------------------------------------------
# While the live feed is down there is no broadcast to show a cycle, so the
# cloud snapshot is read again at these moments after an action.
AFTER_ACTION_REFRESH_DELAYS: Final = (5, 15, 40)
LIVE_POSITION_SECONDS: Final = 30  # a broadcast position outranks the cloud's for this long
BROADCAST_CONFIRM_SECONDS: Final = 8  # wait for the broadcast that confirms a mode switch

# --- the live feed ---------------------------------------------------------------------------------
# The pause before the feed is opened again grows with every connection that did not last.
# Each pause is also spread at random around its length: the installs that lose the broker
# together would otherwise all come back in the same second.
FEED_BACKOFF_FLOOR: Final = 4  # the first pause
FEED_BACKOFF_GROWTH: Final = 3  # each next pause is this many times longer
FEED_BACKOFF_CEILING: Final = 240  # and never longer than this
FEED_BACKOFF_SPREAD: Final = 0.25  # a pause falls this far either side of its length
FEED_HEALTHY_SECONDS: Final = 45  # a connection that lasted this long starts the pauses over
FEED_STALE_SECONDS: Final = 90  # a silent connection is rebuilt after this
FEED_CHECK_SECONDS: Final = 2.0  # how often the connection is looked at

# --- the broker -------------------------------------------------------------------------------------
CLIENT_BUNDLE_SECONDS: Final = 6 * 3600  # lifetime of an issued client certificate in our cache

# --- links ---------------------------------------------------------------------------------------
REPO_URL: Final = "https://github.com/Centurion-Systems/MyCentsys-Cloud"
ISSUE_TRACKER_URL: Final = REPO_URL + "/issues"
DOCUMENTATION_URL: Final = REPO_URL + "#readme"
SUPPORTED_DEVICES_URL: Final = REPO_URL + "#supported-devices"
