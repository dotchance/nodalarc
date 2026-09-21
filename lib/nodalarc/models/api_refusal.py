"""The client-visible reason a VS-API request was refused."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ApiRefusal(BaseModel):
    """Stable, path-free refusal evidence for one request.

    ``code`` is the refusing component's own vocabulary (an exception family's
    enum value or one fixed name per family). ``message`` names the declared
    inputs and catalog references involved; the host filesystem and other
    exceptions' text never appear in it. ``cause_type`` is the class name of a
    wrapped cause when the refusal carries one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    cause_type: str | None = Field(default=None, min_length=1)
