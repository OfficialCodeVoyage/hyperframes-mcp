"""The package's circuit breakers can share a process with a host's, so their names must not be a host's."""

from circuitbreaker import CircuitBreakerMonitor

from hyperframes_mcp.providers.image import client, fal_client  # noqa: F401  (importing registers the breakers)


def test_every_breaker_the_package_registers_is_named_under_the_package():
    # CircuitBreakerMonitor keys breakers by name: a host with a breaker of the same name (a project that vendored this
    # code had "fal_image" and "openai_image") would lose its entry, and its own tests' reset would skip it.
    names = [name for name in CircuitBreakerMonitor.circuit_breakers if "image" in name]

    assert {"hyperframes_mcp.fal_image", "hyperframes_mcp.openai_image"} <= set(names)
    assert not {"fal_image", "openai_image"} & set(CircuitBreakerMonitor.circuit_breakers)
