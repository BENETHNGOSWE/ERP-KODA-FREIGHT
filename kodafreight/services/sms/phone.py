"""Phone number normalisation.

Kilakona (and the older messaging-service.co.tz broker) expect an international MSISDN
without a leading `+` or `00`, e.g. `255745788788`. The Laravel system passed whatever the
user typed straight through, which silently produced failures for locally formatted numbers.
"""

import re

_NON_DIGIT = re.compile(r"[^0-9]")


class InvalidPhoneNumber(ValueError):
    pass


def normalise(raw, country_code: str = "255", national_length: int = 9) -> str:
    """Return the MSISDN form of `raw` or raise InvalidPhoneNumber.

    Accepted inputs (country_code=255, national_length=9):
        +255745788788, 00255745788788, 255745788788, 0745788788, 745788788,
        "0745 788 788", "0745-788-788"
    """
    if raw is None:
        raise InvalidPhoneNumber("Phone number is required")

    text = str(raw).strip()
    if not text:
        raise InvalidPhoneNumber("Phone number is required")

    digits = _NON_DIGIT.sub("", text)
    if not digits:
        raise InvalidPhoneNumber(f"'{raw}' contains no digits")

    cc = _NON_DIGIT.sub("", str(country_code or "")) or "255"

    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith(cc) and len(digits) == len(cc) + national_length:
        msisdn = digits
    elif digits.startswith("0") and len(digits) == national_length + 1:
        msisdn = cc + digits[1:]
    elif len(digits) == national_length:
        msisdn = cc + digits
    elif len(digits) > national_length + 1 and not digits.startswith("0"):
        # Already an international number of another country.
        msisdn = digits
    else:
        raise InvalidPhoneNumber(f"'{raw}' is not a valid phone number")

    if not (8 <= len(msisdn) <= 15):
        raise InvalidPhoneNumber(f"'{raw}' is not a valid MSISDN")

    return msisdn


def try_normalise(raw, country_code: str = "255", national_length: int = 9):
    """Same as `normalise` but returns None instead of raising."""
    try:
        return normalise(raw, country_code, national_length)
    except InvalidPhoneNumber:
        return None
