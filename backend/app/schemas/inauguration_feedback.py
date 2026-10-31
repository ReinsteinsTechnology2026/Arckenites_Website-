import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Indian mobile: optional +91 / 91 / 0 prefix, then 10 digits starting 6-9.
_IN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
# Client-generated id for one submission attempt series; the sheet de-duplicates on it.
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def normalize_indian_mobile(raw: str) -> str | None:
    digits = re.sub(r"[\s\-().]", "", raw or "")
    for prefix in ("+91", "91", "0"):
        if digits.startswith(prefix) and len(digits) > 10:
            digits = digits[len(prefix):]
            break
    return digits if _IN_MOBILE_RE.match(digits) else None


class InaugurationFeedbackRequest(BaseModel):
    submission_id: str = Field(min_length=36, max_length=36)
    name: str = Field(min_length=1, max_length=200)
    domain: Literal["IT", "Cloud", "Networking", "Cybersecurity", "Others"]
    domain_other: str | None = Field(default=None, max_length=200)
    overall_session: Literal["Excellent", "Very Good", "Good", "Average"]
    usefulness: Literal["Very Useful", "Useful", "Average", "Not Useful"]
    learned_something: Literal["Yes", "No"]
    liked_topic: Literal["AI", "Cloud", "Cybersecurity", "Networking", "Other"]
    liked_topic_other: str | None = Field(default=None, max_length=200)
    attend_future: Literal["Yes", "Maybe", "No"]
    suggestion: str | None = Field(default=None, max_length=2000)
    contact_preference: Literal["yes", "maybe", "no"]
    contact_number: str | None = Field(default=None, max_length=30)

    @field_validator("name", "domain_other", "liked_topic_other", "suggestion", "contact_number")
    @classmethod
    def strip_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("submission_id")
    @classmethod
    def check_submission_id(cls, value: str) -> str:
        if not _UUID_RE.match(value):
            raise ValueError("Invalid submission id.")
        return value.lower()

    @model_validator(mode="after")
    def check_conditionals(self):
        if not self.name:
            raise ValueError("Enter your name.")
        if self.domain == "Others":
            if not self.domain_other:
                raise ValueError("Please specify your domain.")
        else:
            self.domain_other = None
        if self.liked_topic != "Other":
            self.liked_topic_other = None
        if self.contact_preference == "no":
            self.contact_number = None
        else:
            normalized = normalize_indian_mobile(self.contact_number or "")
            if not normalized:
                raise ValueError("Enter a valid 10-digit Indian mobile number.")
            self.contact_number = normalized
        return self
