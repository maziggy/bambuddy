"""Pydantic schemas for the onboarding tour API.

See docs/onboarding-tour-plan.md (Appendix B) for the state model.
"""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

# Step IDs use dotted-numeric form ("1.2", "2.2b", "3.7"); bound to 40 chars
# so the full "tour_in_progress:<step>" string fits inside VARCHAR(64).
_STEP_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,40}$")

_TERMINAL_STATUSES = frozenset({"dismissed", "snoozed", "completed_tour"})


class OnboardingResponse(BaseModel):
    """Current onboarding state for the authenticated user.

    `status` is null for users who have not yet seen the welcome modal.
    """

    status: str | None = None
    snoozed_until: datetime | None = None


class OnboardingUpdate(BaseModel):
    """PATCH body for /api/v1/users/me/onboarding.

    The `dismissed_at_migration` value is intentionally NOT acceptable here —
    it is set once by the column-add migration to mark pre-existing users as
    not-eligible and must not be replayable from the API.
    """

    status: str = Field(..., max_length=64)
    snoozed_until: datetime | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v in _TERMINAL_STATUSES:
            return v
        if v.startswith("tour_in_progress:"):
            step_id = v[len("tour_in_progress:") :]
            if _STEP_ID_RE.match(step_id):
                return v
        raise ValueError("status must be one of: dismissed, snoozed, completed_tour, or tour_in_progress:<step_id>")

    @model_validator(mode="after")
    def validate_snooze_coherence(self) -> "OnboardingUpdate":
        if self.status == "snoozed":
            if self.snoozed_until is None:
                raise ValueError("snoozed_until is required when status='snoozed'")
        elif self.snoozed_until is not None:
            raise ValueError("snoozed_until is only valid when status='snoozed'")
        return self
