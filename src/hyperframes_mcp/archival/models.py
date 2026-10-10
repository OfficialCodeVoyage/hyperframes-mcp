"""Data models for archival image candidates and their rights provenance.

The models are deliberately conservative: a candidate whose rights are not
known defaults to ``unknown`` with ``needs_review`` set, and the model refuses
to represent ``unknown`` without review or ``public_domain``/``cc0`` without a
recorded source field that justified it.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RightsStatus(StrEnum):
    """What the source says about reuse of an item.

    ``public_domain`` and ``cc0`` are assigned only on an explicit signal from
    the source. Anything ambiguous is ``unknown``.
    """

    PUBLIC_DOMAIN = "public_domain"
    CC0 = "cc0"
    ATTRIBUTION_REQUIRED = "attribution_required"
    SHARE_ALIKE = "share_alike"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"

    @property
    def needs_credit(self) -> bool:
        """True for licences that require a credit in the description."""
        return self in (RightsStatus.ATTRIBUTION_REQUIRED, RightsStatus.SHARE_ALIKE)


class Dimensions(BaseModel):
    """Pixel dimensions of the file behind ``direct_file_url``."""

    model_config = ConfigDict(frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @classmethod
    def parse(cls, width: Any, height: Any) -> "Dimensions | None":
        """Dimensions from loosely typed values, or ``None`` if either is
        missing, non-numeric or not positive."""
        try:
            return cls(width=int(width), height=int(height))
        except (TypeError, ValueError):
            return None

    @property
    def pixels(self) -> int:
        """Total pixel count."""
        return self.width * self.height


class Provenance(BaseModel):
    """Where a candidate and its rights fields came from.

    Kept per item so a Content ID dispute can show the licence text the
    source gave at the time of retrieval.
    """

    model_config = ConfigDict(frozen=True)

    retrieved_at: datetime
    api_url: str
    raw_rights_fields: dict[str, Any] = Field(default_factory=dict)


class FileRecord(BaseModel):
    """The file that was downloaded for an item: what a source packet needs to find it again.

    Added to the licence record when the file is saved (``ArchivalService``); a candidate straight
    from a provider has none.
    """

    model_config = ConfigDict(frozen=True)

    sha256: str
    size_bytes: int = Field(ge=0)
    downloaded_at: datetime


class RejectedPick(BaseModel):
    """A candidate the picture judge looked at and did not use, with its reason."""

    model_config = ConfigDict(frozen=True)

    provider: str
    provider_id: str
    title: str
    verdict: Literal["partial", "wrong"]
    reason: str
    query: str


class PictureCheck(BaseModel):
    """What the picture judge said about the picture that was used, so a person can audit the pick.

    ``verdict`` is ``fits`` (it shows what the cue asks for), ``partial`` (the right kind of picture
    but not the specific one: it only stands in for the beat) or ``unchecked`` (the judge could not
    run, or the picture was not judged by sight; the pick came from the text ranking alone and
    ``reason`` says why). A ``wrong`` picture is never used, so it is never here. ``query`` is the
    query that found the picture and ``round`` the search round (1 is the cue's own query);
    ``rejected`` lists the other pictures the judge turned down for this cue.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Literal["fits", "partial", "unchecked"]
    reason: str
    caption: str
    query: str
    round: int = Field(ge=1)
    judged_at: datetime
    model: str | None = None
    rejected: tuple[RejectedPick, ...] = ()


class ArchivalCandidate(BaseModel):
    """One archival image found at a provider, with its rights assessment."""

    model_config = ConfigDict(frozen=True)

    provider: str
    provider_id: str
    source_url: str
    direct_file_url: str | None = None
    title: str
    creator: str | None = None
    date: str | None = None
    description: str | None = None
    dimensions: Dimensions | None = None
    licence_id: str | None = None
    licence_url: str | None = None
    attribution_text: str | None = None
    rights_statement: str | None = None
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    needs_review: bool = True
    review_reasons: tuple[str, ...] = ()
    provenance: Provenance
    file: FileRecord | None = None
    picture_check: PictureCheck | None = None

    @model_validator(mode="after")
    def _check_rights_invariants(self) -> "ArchivalCandidate":
        """Make ambiguous or unevidenced rights states unrepresentable."""
        if self.rights_status is RightsStatus.UNKNOWN and not self.needs_review:
            raise ValueError("unknown rights status must set needs_review")
        if (
            self.rights_status in (RightsStatus.PUBLIC_DOMAIN, RightsStatus.CC0)
            and not self.provenance.raw_rights_fields
        ):
            raise ValueError(
                f"{self.rights_status.value} requires the source rights fields "
                "that justify it in provenance.raw_rights_fields"
            )
        return self

    @property
    def usable_by_default(self) -> bool:
        """True only for items that may go into a video without a human decision.

        Public domain, CC0 and attribution-required items qualify, and only
        when nothing flagged them for review. ``share_alike``, ``unknown`` and
        ``restricted`` items never do.
        """
        return not self.needs_review and self.rights_status in (
            RightsStatus.PUBLIC_DOMAIN,
            RightsStatus.CC0,
            RightsStatus.ATTRIBUTION_REQUIRED,
        )
