"""Canonical forms of the credentials that identify a Community account.

Used both to compare a new registration against existing accounts and to
store the normalized mobile number that the database enforces as unique.
The same functions must be used everywhere a value is stored or compared,
or the uniqueness guarantee silently stops working.

Mobile assumption: the registration form has no country selector, and the
existing data is 10-digit national numbers (e.g. 9812345678). Numbers are
therefore reduced to national digits: "+91 98123 45678", "0091-9812345678",
"098123 45678" and "9812345678" all normalize to "9812345678". A number that
genuinely belongs to another country is kept as its full digit string, so
it cannot collide with a national number by accident.
"""

import re


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def normalize_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):          # international dialling prefix
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("91"):   # India country code
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):  # national trunk prefix
        digits = digits[1:]
    return digits
