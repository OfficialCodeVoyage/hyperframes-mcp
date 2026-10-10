"""Attribution strings for video descriptions.

CC BY and CC BY-SA need a credit in the description (the rights
rules in rights.py). Public-domain and CC0 items get a courtesy credit so the
provenance is visible. Share-alike, unknown, restricted and review-flagged
items are never credited by default because they are not for default use.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from hyperframes_mcp.archival.models import ArchivalCandidate, RightsStatus

PROVIDER_LABELS: dict[str, str] = {
    "loc": "Library of Congress",
    "chronicling_america": "Library of Congress, Chronicling America",
    "wikimedia": "Wikimedia Commons",
    "internet_archive": "Internet Archive",
    "openverse": "Openverse",  # its pictures come from many collections; the landing page URL names the one
    "freesound": "Freesound",  # the sound module (src/sound) credits through the same line
    "jamendo": "Jamendo",
}


def format_credit_line(
    *,
    lead: str,
    provider: str,
    source_url: str,
    status: RightsStatus,
    licence_id: str | None,
    licence_url: str | None,
) -> str:
    """``lead`` (the title and creator), the source and the licence clause as one credit line."""
    source = f"Source: {PROVIDER_LABELS.get(provider, provider)}, {source_url}"
    if status.needs_credit:
        licence = f"Licensed under {licence_id or 'the licence stated at the source'}"
        if licence_url:
            licence += f" ({licence_url})"
    elif status is RightsStatus.PUBLIC_DOMAIN:
        licence = "Public domain"
    elif status is RightsStatus.CC0:
        licence = "CC0 1.0 public domain dedication"
    elif status is RightsStatus.RESTRICTED:
        licence = "Restricted: not cleared for use"
    else:
        licence = "Rights status not confirmed"
    if not lead.endswith("."):
        lead += "."
    return f"{lead} {source}. {licence}."


def build_credit_line(candidate: ArchivalCandidate, *, required_text: str | None = None) -> str:
    """One credit line: title, creator, source and licence.

    ``required_text`` is an attribution string the rights holder asked for
    (the Commons ``Attribution`` field); it replaces the title and creator.
    """
    if required_text:
        lead = required_text.strip()
    else:
        lead = f'"{candidate.title}"'
        if candidate.creator:
            lead += f" by {candidate.creator}"
        if candidate.date:
            lead += f" ({candidate.date})"
    return format_credit_line(
        lead=lead,
        provider=candidate.provider,
        source_url=candidate.source_url,
        status=candidate.rights_status,
        licence_id=candidate.licence_id,
        licence_url=candidate.licence_url,
    )


def credit_line(candidate: ArchivalCandidate) -> str:
    """The candidate's stored ``attribution_text``, or one built from its fields."""
    return candidate.attribution_text or build_credit_line(candidate)


def with_attribution(candidate: ArchivalCandidate, *, required_text: str | None = None) -> ArchivalCandidate:
    """Return ``candidate`` with ``attribution_text`` filled in.

    An item whose licence needs a credit but that has no creator and no
    rights-holder attribution text cannot be credited, so it is also flagged
    for review. Every provider client finishes its candidates with this.
    """
    update: dict[str, object] = {"attribution_text": build_credit_line(candidate, required_text=required_text)}
    if candidate.rights_status.needs_credit and not (candidate.creator or required_text):
        update["needs_review"] = True
        update["review_reasons"] = (
            *candidate.review_reasons,
            "no creator or attribution text to credit",
        )
    return candidate.model_copy(update=update)


@dataclass(frozen=True)
class SkippedCandidate:
    """A candidate left out of the credits, with the reason."""

    candidate: ArchivalCandidate
    reason: str


@dataclass(frozen=True)
class AttributionBlock:
    """Credits for the description plus the candidates that were left out."""

    credits: tuple[str, ...]
    skipped: tuple[SkippedCandidate, ...]

    @property
    def text(self) -> str:
        """The block as description text, or ``""`` when there is nothing to credit."""
        if not self.credits:
            return ""
        return "Image credits:\n" + "\n".join(f"- {line}" for line in self.credits)


def _skip_reason(candidate: ArchivalCandidate, allow_share_alike: bool) -> str | None:
    status = candidate.rights_status
    if status is RightsStatus.SHARE_ALIKE and not allow_share_alike:
        return "share-alike licence: not for default use"
    if status is RightsStatus.UNKNOWN:
        return "rights status unknown"
    if status is RightsStatus.RESTRICTED:
        return "restricted licence"
    if candidate.needs_review:
        reasons = "; ".join(candidate.review_reasons) or "flagged for review"
        return f"needs review: {reasons}"
    return None


def build_attribution_block(
    candidates: Iterable[ArchivalCandidate], *, allow_share_alike: bool = False
) -> AttributionBlock:
    """Build the description credits for the candidates used in a video.

    Items that must be credited (attribution-required, and share-alike when
    ``allow_share_alike`` is set) come first, then public-domain and CC0
    courtesy credits. Anything that is not usable by default is reported in
    ``skipped`` instead of being credited, so a share-alike or unreviewed item
    cannot slip into a description unnoticed.
    """
    required: list[str] = []
    courtesy: list[str] = []
    skipped: list[SkippedCandidate] = []
    for candidate in candidates:
        if reason := _skip_reason(candidate, allow_share_alike):
            skipped.append(SkippedCandidate(candidate, reason))
            continue
        bucket = required if candidate.rights_status.needs_credit else courtesy
        bucket.append(credit_line(candidate))
    credits = tuple(dict.fromkeys([*required, *courtesy]))
    return AttributionBlock(credits=credits, skipped=tuple(skipped))
