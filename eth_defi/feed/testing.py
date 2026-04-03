"""Reusable test helpers for :mod:`eth_defi.feed`."""

from pathlib import Path

from eth_defi.feed.sources import TrackedPostSource


def make_test_tracked_source(**kwargs) -> TrackedPostSource:
    """Create a tracked source fixture object for feed tests."""

    return TrackedPostSource(
        feeder_id=kwargs.get("feeder_id", "gearbox"),
        name=kwargs.get("name", "Gearbox"),
        role=kwargs.get("role", "protocol"),
        website=kwargs.get("website"),
        source_type=kwargs.get("source_type", "rss"),
        source_key=kwargs.get("source_key", "https://example.com/feed.xml"),
        canonical_url=kwargs.get("canonical_url", "https://example.com/feed.xml"),
        mapping_file=kwargs.get("mapping_file", Path("/tmp/test.yaml")),
    )
