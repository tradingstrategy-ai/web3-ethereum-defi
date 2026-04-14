"""Tests for vault post-processing error handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from eth_defi.vault import post_processing


def test_upload_top_vaults_json_to_configured_buckets_continues_after_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alternative bucket upload should still run after a primary failure."""
    output_path = tmp_path / "top_vaults_by_chain.json"
    output_path.write_text("{}", encoding="utf-8")
    upload_attempts: list[str] = []

    def fake_upload_file_to_r2(*, bucket_name: str, **_: object) -> bool:
        upload_attempts.append(bucket_name)
        if bucket_name == "public-bucket":
            raise RuntimeError("403 Forbidden")
        return True

    monkeypatch.setattr(post_processing, "upload_file_to_r2", fake_upload_file_to_r2)

    success = post_processing._upload_top_vaults_json_to_configured_buckets(
        s3_client=object(),
        output_path=output_path,
        bucket_name="public-bucket",
        endpoint_url="https://db6295230fa08e641c3ce159dcda30a8.r2.cloudflarestorage.com",
        object_key="top_vaults_by_chain.json",
        access_key_id="e24301234567dac",
        alt_bucket_name="private-bucket",
    )

    assert success is False
    assert upload_attempts == ["public-bucket", "private-bucket"]
