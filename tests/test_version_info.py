"""Test Docker git version stamp reading."""

import json
from pathlib import Path

import pyarrow as pa

from eth_defi.version_info import PARQUET_VERSION_METADATA_KEY, UNSPECIFIED_SENTINEL, VersionInfo, stamp_parquet_schema_metadata


def test_read_docker_version(tmp_path: Path):
    """VersionInfo reads stamp files and normalises missing values to None.

    The Docker build writes ``unspecified`` placeholders when a version
    build ARG is not passed — most commonly ``GIT_VERSION_TAG`` on an
    untagged commit — and consumers must see those as ``None`` rather
    than the sentinel string leaking into JSON exports.

    1. Read a fully stamped root and assert all fields populated
    2. Read a root where tag holds the unspecified sentinel and assert tag is None
    3. Read a root with an empty stamp file and assert it is None
    4. Read an unstamped root and assert every field is None
    """
    # 1. Fully stamped image
    (tmp_path / "GIT_VERSION_TAG.txt").write_text("v0.31\n")
    (tmp_path / "GIT_COMMIT_MESSAGE.txt").write_text("feat: stamp version\n")
    (tmp_path / "GIT_VERSION_HASH.txt").write_text("4cea3aa3deadbeef\n")
    version = VersionInfo.read_docker_version(tmp_path)
    assert version.tag == "v0.31"
    assert version.commit_message == "feat: stamp version"
    assert version.commit_hash == "4cea3aa3deadbeef"
    assert version.as_dict() == {
        "tag": "v0.31",
        "commit_message": "feat: stamp version",
        "commit_hash": "4cea3aa3deadbeef",
    }

    # 2. Untagged build: tag ARG not passed, Dockerfile default sentinel written
    (tmp_path / "GIT_VERSION_TAG.txt").write_text(f"{UNSPECIFIED_SENTINEL}\n")
    version = VersionInfo.read_docker_version(tmp_path)
    assert version.tag is None
    assert version.commit_hash == "4cea3aa3deadbeef"

    # 3. Empty stamp file is normalised to None
    (tmp_path / "GIT_VERSION_TAG.txt").write_text("\n")
    version = VersionInfo.read_docker_version(tmp_path)
    assert version.tag is None

    # 4. No stamp files at all, e.g. running from a source checkout
    unstamped = tmp_path / "unstamped"
    unstamped.mkdir()
    version = VersionInfo.read_docker_version(unstamped)
    assert version.tag is None
    assert version.commit_message is None
    assert version.commit_hash is None


def test_stamp_parquet_schema_metadata() -> None:
    """Parquet provenance preserves existing metadata and the JSON version shape."""
    version = VersionInfo(tag="v0.31", commit_message="feat: stamp parquet", commit_hash="4cea3aa3deadbeef")
    schema = pa.schema([pa.field("chain", pa.uint32())], metadata={b"pandas": b"{}"})

    stamped = stamp_parquet_schema_metadata(schema, version)

    assert stamped.metadata[b"pandas"] == b"{}"
    assert json.loads(stamped.metadata[PARQUET_VERSION_METADATA_KEY]) == version.as_dict()
