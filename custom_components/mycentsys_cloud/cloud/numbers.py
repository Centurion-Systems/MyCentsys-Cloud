"""Phone number handling.

The backends identify an account by its number in international form
(``+27820000000``). The app builds that from the number the user types and the
country they pick, dropping the national trunk prefix where the country has
one; the same rules live here so the integration lands on the same account.
"""

from __future__ import annotations

import re

__all__ = ["digits", "normalize", "to_international", "same_number"]

_NON_DIGIT = re.compile(r"\D")


def digits(number: str | None) -> str:
    """Only the digits of a number (no ``+``, spaces or punctuation)."""
    return _NON_DIGIT.sub("", number or "")


def normalize(number: str) -> str:
    """``+<digits>`` for a number already carrying its country code.

    ``00`` is accepted as the international prefix. A number without either is
    returned as bare digits; callers that know the country use
    :func:`to_international` instead.
    """
    text = (number or "").strip()
    if text.startswith("+"):
        return "+" + digits(text)
    body = digits(text)
    if body.startswith("00"):
        return "+" + body[2:]
    return body


def _strip_trunk_prefix(national: str, dial_code: str) -> str:
    """Drop the trunk prefix a caller dials before a national number.

    Most countries use ``0``. Mexico (``+52``) and Mongolia (``+976``) use
    two-digit prefixes, Hungary (``+36``) uses ``06``, and Italy, San Marino, the
    Vatican and Ivory Coast have none (a leading zero is part of the number).
    """
    if len(national) < 3:
        return national
    if dial_code == "52":
        return national[2:] if national.startswith("01") else national
    if dial_code == "976":
        return national[2:] if national.startswith(("01", "02")) else national
    if dial_code == "36":
        return national[2:] if national.startswith("06") else national
    if dial_code in ("39", "378", "379", "225"):
        return national
    return national[1:] if national.startswith("0") else national


def to_international(number: str, dial_code: int | str) -> str:
    """Turn a number as dialled inside a country into its international form.

    A number that already starts with the dialling code (with or without ``+``
    or ``00``) is kept as it is.
    """
    code = digits(str(dial_code))
    if not code:
        raise ValueError("A dialling code is required")
    text = (number or "").strip()
    if text.startswith("+") or digits(text).startswith("00"):
        body = digits(text)
        body = body[2:] if body.startswith("00") else body
        return "+" + body
    body = digits(text)
    if body.startswith(code) and len(body) > len(code) + 4:
        return "+" + body
    return "+" + code + _strip_trunk_prefix(body, code)


def same_number(first: str | None, second: str | None) -> bool:
    """Whether two numbers are the same account, comparing digits only."""
    one, two = digits(first), digits(second)
    return bool(one) and one == two
