"""Canonical email login validation shared by student and trainer provisioning."""
import re


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_login_email(value: str) -> str:
    if not isinstance(value, str):
        return value
    value = value.strip().lower()
    if not EMAIL_RE.fullmatch(value) or any(c in value for c in '<>"(),;:\\[]'):
        raise ValueError("Please enter a valid email address.")
    local, domain = value.rsplit("@", 1)
    labels = domain.split(".")
    if (len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
        raise ValueError("Please enter a valid email address.")
    return value
