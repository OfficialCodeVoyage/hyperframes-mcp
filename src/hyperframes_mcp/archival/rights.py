"""Conservative rights classification for archival providers.

Principles:

* ``public_domain`` and ``cc0`` are assigned only on an explicit signal the
  source itself gives: a CC0 or Public Domain Mark licence URL, a Commons
  public-domain licence template, or (Library of Congress only) a rights
  statement that says "public domain" or names a US Government work.
* Age is never a signal. "No known restrictions", "not evaluated" and similar
  wording is not a public-domain statement and becomes ``unknown`` with
  ``needs_review``.
* Contradictory or unrecognised evidence becomes ``unknown`` with
  ``needs_review``. When two signals disagree the more restrictive one wins.
* Non-commercial and no-derivatives licences are ``restricted``: the profile
  is monetised and edits (crop, pan, zoom) every image.

Every assessor takes plain values so it can be tested without HTTP.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from hyperframes_mcp.archival.models import RightsStatus

# Lower is better. Public domain and CC0 carry no obligations, attribution
# items need a credit, share-alike items are not for default use, unknown items
# need a human, restricted items cannot be used.
RIGHTS_ORDER: dict[RightsStatus, int] = {
    RightsStatus.PUBLIC_DOMAIN: 0,
    RightsStatus.CC0: 0,
    RightsStatus.ATTRIBUTION_REQUIRED: 1,
    RightsStatus.SHARE_ALIKE: 2,
    RightsStatus.UNKNOWN: 3,
    RightsStatus.RESTRICTED: 4,
}

# Creative Commons licence family -> status. NC and ND variants are restricted.
CC_STATUS_BY_VARIANT: dict[str, RightsStatus] = {
    "by": RightsStatus.ATTRIBUTION_REQUIRED,
    "by-sa": RightsStatus.SHARE_ALIKE,
    "by-nc": RightsStatus.RESTRICTED,
    "by-nd": RightsStatus.RESTRICTED,
    "by-nc-sa": RightsStatus.RESTRICTED,
    "by-nc-nd": RightsStatus.RESTRICTED,
}

# Commons "License" template keys and short names that state public domain.
WIKIMEDIA_PUBLIC_DOMAIN_TOKENS: frozenset[str] = frozenset({"pd", "public-domain"})
WIKIMEDIA_PUBLIC_DOMAIN_PREFIXES: tuple[str, ...] = ("pd-usgov",)


@dataclass(frozen=True)
class RightsAssessment:
    """Outcome of classifying one item's rights fields."""

    status: RightsStatus
    needs_review: bool
    licence_id: str | None = None
    licence_url: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CcLicence:
    """A recognised Creative Commons or public-domain licence."""

    status: RightsStatus
    licence_id: str
    licence_url: str | None = None


def unknown(
    reason: str,
    licence_id: str | None = None,
    licence_url: str | None = None,
) -> RightsAssessment:
    """An ``unknown`` assessment, which always needs review."""
    return RightsAssessment(RightsStatus.UNKNOWN, True, licence_id, licence_url, (reason,))


def worse(a: RightsStatus, b: RightsStatus) -> RightsStatus:
    """The more restrictive of two statuses."""
    return a if RIGHTS_ORDER[a] >= RIGHTS_ORDER[b] else b


# ---------------------------------------------------------------- CC parsing

_DEED = r"(?:/deed(?:\.[A-Za-z_-]+)?)?"
_CC_LICENCE_URL = re.compile(
    r"^https?://(?:www\.)?creativecommons\.org/licenses/"
    r"(?P<variant>by(?:-nc)?(?:-sa|-nd)?)/(?P<version>\d\.\d)"
    r"(?:/[a-z]{2,3})?" + _DEED + "$",
    re.IGNORECASE,
)
_CC_PUBLICDOMAIN_URL = re.compile(
    r"^https?://(?:www\.)?creativecommons\.org/publicdomain/"
    r"(?P<kind>zero|mark)/1\.0" + _DEED + "$",
    re.IGNORECASE,
)
_CC_TOKEN = re.compile(r"^cc-(?P<variant>by(?:-nc)?(?:-sa|-nd)?)-(?P<version>\d\.\d)(?:-[a-z0-9]+)*$")
_CC0_TOKENS = frozenset({"cc0", "cc0-1.0", "cc-zero"})
_PDM_TOKENS = frozenset({"pdm", "pdm-1.0", "public-domain-mark", "public-domain-mark-1.0"})


def _cc_licence(variant: str, version: str, url: str | None) -> CcLicence:
    variant = variant.lower()
    return CcLicence(CC_STATUS_BY_VARIANT[variant], f"CC-{variant.upper()}-{version}", url)


def parse_cc_url(url: str | None) -> CcLicence | None:
    """Recognise a Creative Commons licence or public-domain tool URL.

    Returns ``None`` for anything that is not an exact CC URL, so an
    unrecognised URL is never treated as a licence.
    """
    if not url:
        return None
    cleaned = url.strip().split("#")[0].split("?")[0].rstrip("/")
    if match := _CC_LICENCE_URL.match(cleaned):
        return _cc_licence(match["variant"], match["version"], url.strip())
    if match := _CC_PUBLICDOMAIN_URL.match(cleaned):
        if match["kind"].lower() == "zero":
            return CcLicence(RightsStatus.CC0, "CC0-1.0", url.strip())
        return CcLicence(RightsStatus.PUBLIC_DOMAIN, "PDM-1.0", url.strip())
    return None


def _normalise_token(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def parse_cc_token(value: str | None) -> CcLicence | None:
    """Recognise a licence name such as ``cc-by-sa-4.0`` or ``CC BY-SA 4.0``.

    Compound names ("CC BY-SA 3.0, GFDL") are not recognised.
    """
    if not value:
        return None
    token = _normalise_token(value)
    if token in _CC0_TOKENS:
        return CcLicence(RightsStatus.CC0, "CC0-1.0")
    if token in _PDM_TOKENS:
        return CcLicence(RightsStatus.PUBLIC_DOMAIN, "PDM-1.0")
    if match := _CC_TOKEN.match(token):
        return _cc_licence(match["variant"], match["version"], None)
    return None


# ------------------------------------------------------- free-text statements


class TextSignal(Enum):
    """What a free-text rights statement says."""

    EXPLICIT_PUBLIC_DOMAIN = "explicit_public_domain"
    RESTRICTED = "restricted"
    DOUBT = "doubt"
    NEUTRAL = "neutral"


# Wording that admits the status is not settled. Checked first, so any doubt
# keeps an item out of public domain. "No known restrictions" is deliberately
# here: it is a statement about what the source knows, not about the law.
_DOUBT = re.compile(
    r"\bno\s+known\b"
    r"|\bnot\s+(?:(?:been|yet)\s+)*(?:evaluated|determined|assessed|reviewed|verified|cleared)\b"
    r"|\b(?:unknown|unclear|uncertain|undetermined|unverified)\b"
    r"|\b(?:not\s+aware|unaware|no\s+information)\b"
    r"|\bmay\s+(?:still\s+)?(?:not\s+)?be\s+(?:protected|restricted|subject|covered|under|in\s+copyright)\b"
    r"|\b(?:possibly|probably|presumably|believed|assumed|likely|might|perhaps)\b"
    r"|\bnot\s+(?:\w+\s+){0,3}public\s+domain\b",
    re.IGNORECASE,
)
# Wording that states a restriction outright.
_RESTRICTED = re.compile(
    r"\ball\s+rights\s+reserved\b"
    r"|©|\(c\)\s*\d{4}|\bcopyright\s+(?:\(c\)\s*)?\d{4}\b"
    r"|\bpermission\b[^.]{0,60}\b(?:required|needed|necessary)\b"
    r"|\b(?:requires?|requiring|without)\b[^.]{0,40}\bpermission\b"
    r"|\bcommercial\s+use\b[^.]{0,80}\b(?:fees?|prohibited|not\s+(?:permitted|allowed)|requires?|subject\s+to)\b"
    r"|\bnon-?commercial\b|\bby[- ]nc\b|\bby[- ]nd\b|\bno[- ]?derivatives?\b"
    r"|\b(?:prohibited|forbidden)\b|\bno\s+reproduction\b"
    r"|\bnot\s+for\s+(?:commercial|publication|reproduction)\b",
    re.IGNORECASE,
)
# An explicit public-domain or US-government-work statement.
_PUBLIC_DOMAIN = re.compile(
    r"\bpublic\s+domain\b"
    r"|\b(?:us|united\s+states|federal)\s+government\s+(?:work|publication)s?\b"
    r"|\bwork\s+of\s+the\s+(?:us|united\s+states)(?:\s+federal)?\s+government\b"
    r"|\bcreated\s+by\s+an?\s+(?:officer\s+or\s+)?employee\s+of\s+the\s+(?:us|united\s+states)\s+government\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:not|never|nor|neither|cannot)\b|n't", re.IGNORECASE)


def classify_rights_text(text: str | None) -> TextSignal:
    """Classify a free-text rights statement.

    Doubt wins over a restriction, which wins over a public-domain phrase. A
    public-domain phrase counts only in a sentence with no negation.
    """
    if not text or not text.strip():
        return TextSignal.NEUTRAL
    flat = re.sub(r"\bU\.\s?S\.", "US", text, flags=re.IGNORECASE)
    if _DOUBT.search(flat):
        return TextSignal.DOUBT
    if _RESTRICTED.search(flat):
        return TextSignal.RESTRICTED
    found = False
    for sentence in re.split(r"(?<=[.!?;])\s+", flat):
        if _PUBLIC_DOMAIN.search(sentence):
            if _NEGATION.search(sentence):
                return TextSignal.DOUBT
            found = True
    return TextSignal.EXPLICIT_PUBLIC_DOMAIN if found else TextSignal.NEUTRAL


def combine_text_signals(signals: Iterable[TextSignal]) -> TextSignal:
    """Combine signals from several statements about one item."""
    collected = list(signals)
    if TextSignal.DOUBT in collected:
        return TextSignal.DOUBT
    if TextSignal.RESTRICTED in collected:
        return TextSignal.RESTRICTED
    if collected and all(s is TextSignal.EXPLICIT_PUBLIC_DOMAIN for s in collected):
        return TextSignal.EXPLICIT_PUBLIC_DOMAIN
    return TextSignal.NEUTRAL


# ----------------------------------------------------------------- providers


def assess_loc(rights_texts: Iterable[str | None], *, access_restricted: bool = False) -> RightsAssessment:
    """Assess a Library of Congress item from its rights statements.

    The statements are the institution's own words, so an explicit "public
    domain" or US-government-work statement is accepted. "No known restrictions
    on publication", "rights status not evaluated" and a missing statement are
    ``unknown``. A set ``access_restricted`` flag blocks public domain.
    """
    texts = list(dict.fromkeys(t.strip() for t in rights_texts if t and t.strip()))
    if not texts:
        return unknown("Library of Congress gave no rights statement")
    signal = combine_text_signals(classify_rights_text(t) for t in texts)
    if signal is TextSignal.RESTRICTED:
        return RightsAssessment(
            RightsStatus.RESTRICTED,
            False,
            reasons=("rights statement states a restriction",),
        )
    if signal is TextSignal.EXPLICIT_PUBLIC_DOMAIN:
        if access_restricted:
            return unknown("public-domain statement but access_restricted is set")
        return RightsAssessment(RightsStatus.PUBLIC_DOMAIN, False)
    if signal is TextSignal.DOUBT:
        return unknown("rights statement does not settle the status")
    return unknown("rights statement is not a clear public-domain or licence statement")


# The paragraph the Library of Congress puts first in the rights field of every Chronicling America
# newspaper page. Recognised whole, so a changed or extended wording is not mistaken for it.
CHRONICLING_AMERICA_LICENCE_ID = "LoC Chronicling America: public domain or no known copyright restrictions"
# The legal page the statement itself points to.
CHRONICLING_AMERICA_LICENCE_URL = "https://www.loc.gov/homepage/legal.html"
_CHRONICLING_AMERICA_PARAGRAPH = re.compile(
    r"The Library of Congress believes that the newspapers in Chronicling America are in the "
    r"public domain or have no known copyright restrictions\b.*?"
    r"\bultimately rests with persons desiring to use the item\.",
    re.IGNORECASE | re.DOTALL,
)


def assess_chronicling_america(
    rights_texts: Iterable[str | None], *, access_restricted: bool = False
) -> RightsAssessment:
    """Assess a Chronicling America newspaper page from its rights statements.

    This is the one place "no known restrictions" is accepted. ``assess_loc`` reads that wording as
    unknown, and every page of the collection carries it ("in the public domain or have no known
    copyright restrictions"), so under that rule no newspaper page could be used. LoC's standing
    paragraph, recognised whole, is accepted as public domain; the licence record names the statement
    and keeps its full text. Anything beside it is judged first: a restriction makes the page
    ``restricted``, any doubt makes it ``unknown``. An ``access_restricted`` page is ``unknown``. A page
    without the paragraph, or whose paragraph is worded differently, goes through ``assess_loc``.
    Age is not a signal: LoC's remark that pages over 95 years old are public domain is not applied
    to a page's date.
    """
    texts = list(dict.fromkeys(t.strip() for t in rights_texts if t and t.strip()))
    flat = [" ".join(t.split()) for t in texts]
    if not any(_CHRONICLING_AMERICA_PARAGRAPH.search(t) for t in flat):
        return assess_loc(texts, access_restricted=access_restricted)
    beside = combine_text_signals(classify_rights_text(_CHRONICLING_AMERICA_PARAGRAPH.sub(" ", t)) for t in flat)
    if beside is TextSignal.RESTRICTED:
        return RightsAssessment(
            RightsStatus.RESTRICTED,
            False,
            reasons=("rights text beside LoC's statement for the collection states a restriction",),
        )
    if beside is TextSignal.DOUBT:
        return unknown("rights text beside LoC's statement for the collection adds doubt")
    if access_restricted:
        return unknown("LoC's statement for the collection, but access_restricted is set")
    return RightsAssessment(
        RightsStatus.PUBLIC_DOMAIN,
        False,
        CHRONICLING_AMERICA_LICENCE_ID,
        CHRONICLING_AMERICA_LICENCE_URL,
    )


def _wikimedia_token(value: str) -> CcLicence | None:
    """Recognise a Commons ``License`` key or ``LicenseShortName``."""
    if licence := parse_cc_token(value):
        return licence
    token = _normalise_token(value)
    if token in WIKIMEDIA_PUBLIC_DOMAIN_TOKENS or token.startswith(WIKIMEDIA_PUBLIC_DOMAIN_PREFIXES):
        return CcLicence(RightsStatus.PUBLIC_DOMAIN, "PD")
    return None


def assess_wikimedia(meta: Mapping[str, str]) -> RightsAssessment:
    """Assess a Wikimedia Commons file from its flattened ``extmetadata``.

    ``meta`` maps extmetadata field names to their string values. Every
    licence-identifying field present (``License``, ``LicenseShortName``,
    ``LicenseUrl``) must be recognised and they must agree; otherwise the file
    is ``unknown``. A public-domain tag on a file Commons also marks as
    copyrighted or attribution-required is ``unknown``. A non-empty
    ``Restrictions`` field (trademark, personality rights, ...) leaves the
    status alone but sets ``needs_review``.
    """
    key = (meta.get("License") or "").strip()
    short = (meta.get("LicenseShortName") or "").strip()
    url = (meta.get("LicenseUrl") or "").strip() or None
    label = short or key or None
    if not (key or short or url):
        return unknown("Commons gave no licence fields")

    found: list[CcLicence] = []
    for field, value, recognise in (
        ("License", key, _wikimedia_token),
        ("LicenseShortName", short, _wikimedia_token),
        ("LicenseUrl", url, parse_cc_url),
    ):
        if not value:
            continue
        licence = recognise(value)
        if licence is None:
            return unknown(f"unrecognised {field}: {value!r}", label, url)
        found.append(licence)
    if len({licence.status for licence in found}) > 1:
        return unknown(
            "licence fields disagree: " + ", ".join(sorted({f.licence_id for f in found})),
            label,
            url,
        )

    status = found[0].status
    licence_id = next((f.licence_id for f in found if f.licence_id != "PD"), found[0].licence_id)
    if status is RightsStatus.PUBLIC_DOMAIN:
        for field, reason in (
            ("Copyrighted", "Commons marks the file as copyrighted"),
            ("AttributionRequired", "Commons says attribution is required"),
        ):
            if (meta.get(field) or "").strip().lower() == "true":
                return unknown(f"tagged public domain but {reason}", licence_id, url)

    reasons: tuple[str, ...] = ()
    restrictions = (meta.get("Restrictions") or "").strip()
    if restrictions:
        reasons = (f"Commons restrictions: {restrictions}",)
    return RightsAssessment(status, bool(reasons), licence_id, url, reasons)


def assess_internet_archive(
    *,
    licenseurl: str | None,
    rights: str | None,
    access_restricted: bool = False,
) -> RightsAssessment:
    """Assess an Internet Archive item.

    Only a recognised ``licenseurl`` can lift an item above ``unknown``: the
    free-text ``rights`` and ``possible-copyright-status`` fields are
    uploader-supplied and are used only to downgrade. Doubt in the rights text
    ("copyright status unknown", "may be protected") overrides a public-domain
    licence URL; a stated restriction overrides everything but an NC/ND
    licence, which is already restricted.
    """
    signal = classify_rights_text(rights)
    licence = parse_cc_url(licenseurl)
    if licence is None:
        reason = "no licenseurl"
        if licenseurl and licenseurl.strip():
            reason = f"unrecognised licenseurl: {licenseurl.strip()!r}"
        if signal is TextSignal.RESTRICTED:
            return RightsAssessment(
                RightsStatus.RESTRICTED,
                False,
                reasons=("rights text states a restriction",),
            )
        return unknown(f"{reason}; free-text rights are not trusted for public domain")

    status = licence.status
    reasons: list[str] = []
    if signal is TextSignal.DOUBT:
        status = worse(status, RightsStatus.UNKNOWN)
        reasons.append("rights text contradicts or qualifies the licenseurl")
    elif signal is TextSignal.RESTRICTED:
        status = RightsStatus.RESTRICTED
        reasons.append("rights text states a restriction")
    if access_restricted:
        status = worse(status, RightsStatus.UNKNOWN)
        reasons.append("access-restricted item")
    return RightsAssessment(
        status,
        status is RightsStatus.UNKNOWN,
        licence.licence_id,
        licence.licence_url,
        tuple(reasons),
    )
