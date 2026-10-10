"""The free archives the media tools search: Library of Congress, Wikimedia Commons, the Internet Archive and
Openverse, each returning ``ArchivalCandidate`` objects with a conservative rights assessment and a provenance record.
``motion.media`` runs the search and the safe download; ``selection`` ranks what comes back.
"""

from hyperframes_mcp.archival.attribution import (
    AttributionBlock,
    SkippedCandidate,
    build_attribution_block,
    build_credit_line,
    credit_line,
    with_attribution,
)
from hyperframes_mcp.archival.base import (
    DEFAULT_USER_AGENT,
    ArchivalProvider,
    ArchivalProviderError,
)
from hyperframes_mcp.archival.download import (
    ArchivalDownloadError,
    DownloadLimits,
    download_candidate,
    download_file,
    download_image,
)
from hyperframes_mcp.archival.internet_archive import InternetArchiveClient
from hyperframes_mcp.archival.loc import LibraryOfCongressClient
from hyperframes_mcp.archival.models import (
    ArchivalCandidate,
    Dimensions,
    Provenance,
    RightsStatus,
)
from hyperframes_mcp.archival.ranking import rank_candidates
from hyperframes_mcp.archival.wikimedia import WikimediaCommonsClient

__all__ = [
    "DEFAULT_USER_AGENT",
    "ArchivalCandidate",
    "ArchivalDownloadError",
    "ArchivalProvider",
    "ArchivalProviderError",
    "AttributionBlock",
    "Dimensions",
    "DownloadLimits",
    "InternetArchiveClient",
    "LibraryOfCongressClient",
    "Provenance",
    "RightsStatus",
    "SkippedCandidate",
    "WikimediaCommonsClient",
    "build_attribution_block",
    "build_credit_line",
    "credit_line",
    "download_candidate",
    "download_file",
    "download_image",
    "rank_candidates",
    "with_attribution",
]
