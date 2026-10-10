"""The licence policy for sound on a monetised video.

Openverse gives each audio item a licence code (``cc0``, ``pdm``, ``by``, ``by-sa``, ``by-nc``, ...),
its version and a deed URL. Both must name the same licence, or the item is ``unknown`` and refused.

* CC0 and the public-domain mark: allowed by default. No obligations, a courtesy credit anyway.
* CC BY: allowed only when attribution is asked for, and then the credit text is required.
* CC BY-SA: refused. Whether a video that uses a share-alike sound becomes a derivative that must
  itself be shared alike is not settled, so this code refuses it rather than guess.
* NC and ND: refused. The profile is monetised, and every sound is cut, mixed and ducked.
* Anything else (``sampling+``, a missing or contradictory licence): refused.

The recognisers are the archival module's (``src/visual_generator/archival/rights.py``).
"""

from dataclasses import dataclass

from hyperframes_mcp.archival.attribution import format_credit_line
from hyperframes_mcp.archival.models import RightsStatus
from hyperframes_mcp.archival.rights import (
    RightsAssessment,
    parse_cc_token,
    parse_cc_url,
    unknown,
)


def assess_openverse_licence(code: str | None, version: str | None, url: str | None) -> RightsAssessment:
    """Classify an Openverse licence code, version and deed URL.

    The three must agree. A missing code or URL, a code Creative Commons does not list, or a URL that
    is not an exact Creative Commons deed gives ``unknown`` with the reason.
    """
    code = (code or "").strip().lower()
    url = (url or "").strip()
    if not code:
        return unknown("Openverse gave no licence")
    token = code if code in ("cc0", "pdm") else f"cc-{code}-{(version or '').strip()}"
    by_code = parse_cc_token(token)
    if by_code is None:
        return unknown(f"unrecognised licence {code!r} {version or ''}".rstrip())
    if not url:
        return unknown(f"no licence URL to check {by_code.licence_id} against", by_code.licence_id)
    by_url = parse_cc_url(url)
    if by_url is None or by_url.licence_id != by_code.licence_id:
        return unknown(
            f"licence code {by_code.licence_id} and licence URL {url!r} do not agree",
            by_code.licence_id,
            url,
        )
    return RightsAssessment(by_code.status, False, by_code.licence_id, url)


@dataclass(frozen=True)
class LicencePolicy:
    """Which licences may be used: CC0 and the public-domain mark, plus CC BY when asked."""

    allow_attribution: bool = False

    @property
    def openverse_filter(self) -> str:
        """The ``license`` query value that asks Openverse for only these licences."""
        return "cc0,pdm,by" if self.allow_attribution else "cc0,pdm"

    def refusal(self, assessment: RightsAssessment) -> str | None:
        """Why the licence may not be used, or None when it may."""
        status = assessment.status
        licence = assessment.licence_id
        if status in (RightsStatus.CC0, RightsStatus.PUBLIC_DOMAIN):
            return None
        if status is RightsStatus.ATTRIBUTION_REQUIRED:
            if self.allow_attribution:
                return None
            return f"{licence} needs attribution, which was not asked for"
        if status is RightsStatus.SHARE_ALIKE:
            return (
                f"{licence} is share-alike: refused for this monetised video "
                "(whether the video becomes a derivative is an open question)"
            )
        if status is RightsStatus.RESTRICTED:
            return f"{licence} forbids commercial use or derivatives: refused for this monetised video"
        return "; ".join(assessment.reasons) or "licence not recognised"


DEFAULT_POLICY = LicencePolicy()


def credit_lead(title: str, creator: str | None) -> str:
    """``"Title" by Creator``; the creator is left out when there is none."""
    return f'"{title}" by {creator}' if creator else f'"{title}"'


def sound_credit_line(
    *,
    title: str,
    creator: str | None,
    provider: str,
    source_url: str,
    status: RightsStatus,
    licence_id: str,
    licence_url: str,
) -> str:
    """One credit line for the video description: title, creator, source and licence.

    CC BY requires it; CC0 and public-domain sounds get it as a courtesy. A CC BY credit for a sound
    that was edited should also say so; the licence asks for an indication of changes.
    """
    return format_credit_line(
        lead=credit_lead(title, creator),
        provider=provider,
        source_url=source_url,
        status=status,
        licence_id=licence_id,
        licence_url=licence_url,
    )
