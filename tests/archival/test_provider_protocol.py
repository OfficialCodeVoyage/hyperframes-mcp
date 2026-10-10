"""Every client satisfies the ArchivalProvider protocol and can be closed."""

import pytest

from hyperframes_mcp.archival import (
    ArchivalProvider,
    InternetArchiveClient,
    LibraryOfCongressClient,
    WikimediaCommonsClient,
)
from hyperframes_mcp.archival.openverse import OpenverseImageClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_cls",
    [
        LibraryOfCongressClient,
        WikimediaCommonsClient,
        InternetArchiveClient,
        OpenverseImageClient,
    ],
)
async def test_client_satisfies_the_provider_protocol_and_closes(client_cls):
    client = client_cls()

    assert isinstance(client, ArchivalProvider)
    assert client.name
    await client.close()
