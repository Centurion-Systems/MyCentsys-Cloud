"""The account's fleet: what the coordinator knows about every device.

A :class:`Fleet` is the value the coordinator publishes to the entities. It
holds one record per device the account can see, each under its fleet key:

* ``<serial>``        a SMART operator            -> :class:`OperatorRecord`
* ``ultra-<id>``        an ULTRA module             -> :class:`ModuleRecord`
* ``access-<guid>``   a gate shared with the account -> :class:`GrantRecord`

Records are plain data; the coordinator replaces or updates them, entities
only read them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from homeassistant.const import CONF_DEVICES

from .calling_codes import CALLING_CODES
from .cloud import products
from .const import CONF_DEVICE_SCOPE, DEVICE_SCOPES, ENTRY_TITLE_PREFIX, SCOPE_ALL, SCOPE_CHOSEN, SCOPE_SMART, SCOPE_ULTRA
from .cloud.models import AccessGrant, CloudSnapshot, ModuleDiagnostics, Operator, UltraModule, UltraReadings
from .cloud.overview import Overview


@dataclass(slots=True)
class OperatorRecord:
    """A SMART operator: the listing, the cloud's snapshot and the operator's own last broadcast."""

    operator: Operator
    snapshot: CloudSnapshot | None = None
    broadcast: Overview | None = None
    # Taken from the header of the operator's broadcasts. Preferred to the
    # listing, whose code can be missing or out of date.
    reported_code: int | None = None

    @property
    def key(self) -> str:
        return self.operator.serial

    @property
    def label(self) -> str:
        return self.operator.label or self.operator.serial

    @property
    def family(self) -> str | None:
        """``slider``, ``swing`` or ``garage``; the operator's own report wins over the listing."""
        return products.family_for_code(self.reported_code) or self.operator.family

    @property
    def is_gate(self) -> bool:
        """Positively a slider or a swing; never a garage door or an unclassified operator."""
        return products.is_gate_family(self.family)

    @property
    def is_garage(self) -> bool:
        return self.family == products.FAMILY_GARAGE

    @property
    def picker_label(self) -> str:
        return f"{self.label} (SMART)"

    @property
    def firmware(self) -> str | None:
        """The firmware line of the device page: core and wireless for a gate, the one version for a garage door."""
        core, wireless = self.operator.firmware, self.operator.ble_firmware
        if core and wireless and not self.is_garage:
            return DETAIL_DIVIDER.join((f"Core: {core}", f"Wireless: {wireless}"))
        return core or wireless


@dataclass(slots=True)
class ModuleRecord:
    """An ULTRA module: its configuration, its live IO states and its diagnostics."""

    module: UltraModule
    readings: UltraReadings | None = None
    diagnostics: ModuleDiagnostics | None = None
    # True on a token SIM, False on a SIM with a network balance, None while that is not known.
    token_sim: bool | None = None

    @property
    def key(self) -> str:
        return self.module.key

    @property
    def label(self) -> str:
        return self.module.label or self.module.key

    @property
    def picker_label(self) -> str:
        return f"{self.label} ({self.module.profile_label})"

    @property
    def firmware(self) -> str | None:
        """Firmware version and number in one line for the device page, once the device reported them."""
        return firmware_line(self.diagnostics)

    @property
    def hardware(self) -> str | None:
        """Antenna and connection in one line for the device page, once the device reported them."""
        return hardware_line(self.diagnostics)


@dataclass(slots=True)
class GrantRecord:
    """A gate another account shared with this one."""

    grant: AccessGrant

    @property
    def key(self) -> str:
        return self.grant.key

    @property
    def label(self) -> str:
        return self.grant.device_label or self.grant.guid

    @property
    def picker_label(self) -> str:
        return f"{self.label} (shared)"


FleetRecord = OperatorRecord | ModuleRecord | GrantRecord


@dataclass(slots=True)
class Fleet:
    """Everything the account can see, by fleet key."""

    operators: dict[str, OperatorRecord] = field(default_factory=dict)
    modules: dict[str, ModuleRecord] = field(default_factory=dict)
    grants: dict[str, GrantRecord] = field(default_factory=dict)

    def records(self) -> dict[str, FleetRecord]:
        """Every record by key, operators first."""
        return {**self.operators, **self.modules, **self.grants}

    def __contains__(self, key: str) -> bool:
        return key in self.operators or key in self.modules or key in self.grants

    def __len__(self) -> int:
        return len(self.operators) + len(self.modules) + len(self.grants)

    def labels(self) -> dict[str, str]:
        """Key -> label for the device picker."""
        return {key: record.picker_label for key, record in self.records().items()}

    def within(self, scope: str, keys: Any = None) -> "Fleet":
        """The part of the fleet a device scope covers.

        ``smart`` and ``ultra`` keep one kind of device, with the shared gates
        of that kind. ``chosen`` keeps the listed keys, and keeps everything
        when the list is empty. Anything else keeps the whole fleet.
        """
        if scope == SCOPE_SMART:
            return Fleet(operators=dict(self.operators), grants={key: record for key, record in self.grants.items() if not record.grant.is_ultra})
        if scope == SCOPE_ULTRA:
            return Fleet(modules=dict(self.modules), grants={key: record for key, record in self.grants.items() if record.grant.is_ultra})
        chosen = set(keys or ()) if scope == SCOPE_CHOSEN else set()
        if not chosen:
            return self
        return Fleet(
            operators={key: record for key, record in self.operators.items() if key in chosen},
            modules={key: record for key, record in self.modules.items() if key in chosen},
            grants={key: record for key, record in self.grants.items() if key in chosen},
        )


def entry_title(number: str | None) -> str:
    """The name of an account's entry: ``MyCentsys Devices: +27820000000``."""
    return f"{ENTRY_TITLE_PREFIX}{number}" if number else ENTRY_TITLE_PREFIX.rstrip(" :")


def scope_of(options: Any) -> str:
    """The device scope stored in an entry's options; a bare list of keys counts as ``chosen``."""
    stored = options.get(CONF_DEVICE_SCOPE) if options else None
    if stored in DEVICE_SCOPES:
        return stored
    return SCOPE_CHOSEN if options and options.get(CONF_DEVICES) else SCOPE_ALL


# The device page shows each of its rows as one line of text and cannot break it,
# so the details that share a row are set apart with this.
DETAIL_DIVIDER = "; "

_DIAL_CODES = frozenset(str(entry.dial) for entry in CALLING_CODES)


def spaced_number(number: str | None) -> str | None:
    """``27820000000`` as ``+27 82 000 0000``: the calling code, then the rest in readable groups."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return None
    code = next((digits[:size] for size in (3, 2, 1) if digits[:size] in _DIAL_CODES), "")
    rest = digits[len(code):]
    # Australian mobile numbers are written in threes; elsewhere the usual split by length is used.
    sizes = (3, 3, 3) if (code, len(rest)) == ("61", 9) else {7: (3, 4), 8: (4, 4), 9: (2, 3, 4), 10: (3, 3, 4)}.get(len(rest))
    if sizes is None:
        groups = [rest[start:start + 3] for start in range(0, len(rest), 3)]
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2:] = [groups[-2] + groups[-1]]
    else:
        groups, start = [], 0
        for size in sizes:
            groups.append(rest[start:start + size])
            start += size
    return "+" + " ".join(part for part in [code, *groups] if part)


def _one_line(*details: str | None) -> str | None:
    """The details that are known, set apart on one line; None when there are none."""
    return DETAIL_DIVIDER.join(detail for detail in details if detail) or None


def firmware_line(diagnostics: ModuleDiagnostics | None) -> str | None:
    """``1.5.1; Number: +27 82 000 0000``; None when nothing is known."""
    if diagnostics is None:
        return None
    return _one_line(
        diagnostics.firmware,
        f"Number: {spaced_number(diagnostics.number)}" if diagnostics.number else None,
    )


def hardware_line(diagnostics: ModuleDiagnostics | None) -> str | None:
    """``Antenna: Internal; Connection: 4G``; None when nothing is known."""
    if diagnostics is None:
        return None
    return _one_line(
        f"Antenna: {diagnostics.antenna.capitalize()}" if diagnostics.antenna else None,
        f"Connection: {diagnostics.network}" if diagnostics.network else None,
    )


def updated(record: Any, **changes: Any) -> Any:
    """A copy of a record with some members replaced."""
    return replace(record, **changes)
