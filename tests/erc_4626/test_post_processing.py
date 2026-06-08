"""Tests for vault post-processing error handling."""

from __future__ import annotations

from pathlib import Path

import brotli
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
    monkeypatch.setenv("R2_DAILY_BACKUP", "false")

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


def test_brotli_upload_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brotli upload sends correct content_encoding, content_type, and object key.

    1. Write a small JSON file
    2. Stub upload_file_to_r2 (raw) and upload_bytes_to_r2 (brotli)
    3. Call _upload_top_vaults_json_to_bucket
    4. Assert brotli upload was called with correct params
    5. Assert the uploaded payload is valid brotli-compressed data
    """
    # 1. Write test JSON
    output_path = tmp_path / "top_vaults_by_chain.json"
    json_content = '{"vaults": []}'
    output_path.write_text(json_content, encoding="utf-8")

    # 2. Stub upload functions
    raw_calls: list[dict] = []
    brotli_calls: list[dict] = []

    def fake_upload_file_to_r2(**kwargs) -> bool:
        raw_calls.append(kwargs)
        return True

    def fake_upload_bytes_to_r2(**kwargs) -> bool:
        brotli_calls.append(kwargs)
        return True

    def fake_calculate_bytes_digest(payload: bytes):
        return "fake-digest"

    monkeypatch.setattr(post_processing, "upload_file_to_r2", fake_upload_file_to_r2)
    monkeypatch.setattr(post_processing, "upload_bytes_to_r2", fake_upload_bytes_to_r2)
    monkeypatch.setattr(post_processing, "calculate_bytes_digest", fake_calculate_bytes_digest)

    # 3. Call the single-bucket upload
    result = post_processing._upload_top_vaults_json_to_bucket(
        s3_client=object(),
        output_path=output_path,
        bucket_name="test-bucket",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        object_key="top_vaults_by_chain.json",
        access_key_id="test-key-12345678",
        bucket_label="primary",
    )

    assert result is True

    # 4. Assert brotli upload params
    assert len(brotli_calls) == 1
    br_call = brotli_calls[0]
    assert br_call["object_name"] == "top_vaults_by_chain.json.br"
    assert br_call["content_type"] == "application/json"
    assert br_call["content_encoding"] == "br"
    assert br_call["skip_if_current"] is True
    assert br_call["source_digest"] == "fake-digest"

    # 5. Assert the payload is valid brotli-compressed data
    decompressed = brotli.decompress(br_call["payload"])
    assert decompressed == json_content.encode("utf-8")


def test_brotli_failure_returns_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When brotli upload fails, raw JSON is already uploaded and function returns False.

    1. Write a small JSON file
    2. Stub upload_file_to_r2 to succeed, upload_bytes_to_r2 to raise
    3. Call _upload_top_vaults_json_to_bucket
    4. Assert raw upload succeeded but function returns False
    """
    # 1. Write test JSON
    output_path = tmp_path / "top_vaults_by_chain.json"
    output_path.write_text('{"vaults": []}', encoding="utf-8")

    # 2. Stub upload functions
    raw_calls: list[dict] = []

    def fake_upload_file_to_r2(**kwargs) -> bool:
        raw_calls.append(kwargs)
        return True

    def fake_upload_bytes_to_r2(**kwargs) -> bool:
        raise RuntimeError("Simulated brotli upload failure")

    def fake_calculate_bytes_digest(payload: bytes):
        return "fake-digest"

    monkeypatch.setattr(post_processing, "upload_file_to_r2", fake_upload_file_to_r2)
    monkeypatch.setattr(post_processing, "upload_bytes_to_r2", fake_upload_bytes_to_r2)
    monkeypatch.setattr(post_processing, "calculate_bytes_digest", fake_calculate_bytes_digest)

    # 3. Call single-bucket upload
    result = post_processing._upload_top_vaults_json_to_bucket(
        s3_client=object(),
        output_path=output_path,
        bucket_name="test-bucket",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        object_key="top_vaults_by_chain.json",
        access_key_id="test-key-12345678",
        bucket_label="primary",
    )

    # 4. Raw upload succeeded but brotli failed → returns False
    assert len(raw_calls) == 1
    assert result is False
