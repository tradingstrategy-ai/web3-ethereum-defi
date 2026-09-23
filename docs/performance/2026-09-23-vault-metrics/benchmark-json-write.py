#!/usr/bin/env python3
"""Compare strict top-vault JSON writers using one saved full export."""

import hashlib
import json
import os
import resource
import time
from pathlib import Path

import orjson
from atomicwrites import atomic_write

from eth_defi.vault.top_vaults_json import validate_strict_json_serialisable


def main() -> None:
    """Time validation and one selected atomic JSON write.

    ``BENCHMARK_VARIANT`` selects the current standard-library writer or
    ``orjson``. The saved snapshot is parsed before timing either writer.

    :return: ``None`` after writing the result artefact.
    """
    variant = os.environ["BENCHMARK_VARIANT"]
    assert variant in {"reference", "orjson"}
    source = Path(os.environ["BENCHMARK_SOURCE_PATH"])
    output = Path(os.environ["BENCHMARK_OUTPUT_PATH"])
    with source.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    started_at = time.perf_counter()
    validate_strict_json_serialisable(data)
    validation_seconds = time.perf_counter() - started_at
    started_at = time.perf_counter()
    if variant == "reference":
        with atomic_write(str(output), mode="w", overwrite=True, encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
    else:
        payload = orjson.dumps(data, option=orjson.OPT_INDENT_2)
        with atomic_write(str(output), mode="wb", overwrite=True) as handle:
            handle.write(payload)
    write_seconds = time.perf_counter() - started_at

    result = {
        "variant": variant,
        "source": str(source),
        "source_size_bytes": source.stat().st_size,
        "validation_seconds": round(validation_seconds, 3),
        "write_seconds": round(write_seconds, 3),
        "total_seconds": round(validation_seconds + write_seconds, 3),
        "output_size_bytes": output.stat().st_size,
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    Path(os.environ["BENCHMARK_RESULT_PATH"]).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)  # noqa: T201 - observable standalone benchmark


if __name__ == "__main__":
    main()
