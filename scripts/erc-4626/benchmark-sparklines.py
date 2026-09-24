"""Benchmark bounded sparkline rendering without publishing to R2."""

from __future__ import annotations

import gzip
import hashlib
import os
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil
from tabulate import tabulate

from eth_defi.research.sparkline import SparklineData
from eth_defi.research.sparkline_export import (
    SPARKLINE_BATCH_SIZE,
    RenderData,
    get_included_vault_ids,
    load_sparkline_price_data,
    prepare_vault_sparklines,
    render_sparklines,
    run_sparkline_export,
)
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir


class ProcessTreeMemorySampler:
    """Sample peak RSS for this process and all of its descendants.

    RSS values are summed over the parent and live child processes. This is a
    useful worker-footprint comparison, but it counts shared pages more than
    once and is not an exact physical-memory measurement.
    """

    def __init__(self, interval_seconds: float = 0.1) -> None:
        """Initialise a periodic process-tree RSS sampler.

        Sampling starts when the instance is entered as a context manager.

        :param interval_seconds:
            Delay between RSS samples.
        :return:
            None.
        """
        self.process = psutil.Process()
        self.interval_seconds = interval_seconds
        self.peak_parent_rss = 0
        self.peak_children_rss = 0
        self.peak_process_tree_rss = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample_until_stopped, daemon=True)

    def __enter__(self) -> ProcessTreeMemorySampler:
        """Start sampling and return this sampler.

        :return:
            The active sampler instance.
        """
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, _exception_type: object, _exception: object, _traceback: object) -> None:
        """Stop sampling after taking a final process-tree measurement.

        :param _exception_type:
            Exception type raised inside the context, if any.
        :param _exception:
            Exception instance raised inside the context, if any.
        :param _traceback:
            Traceback raised inside the context, if any.
        :return:
            None.
        """
        self._sample()
        self._stop.set()
        self._thread.join()

    def _sample_until_stopped(self) -> None:
        """Sample RSS until the context manager signals shutdown.

        :return:
            None.
        """
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self) -> None:
        """Update the peak RSS values from currently live processes.

        Processes that exit or deny access during a sample are ignored.

        :return:
            None.
        """
        try:
            parent_rss = self.process.memory_info().rss
            children_rss = 0
            for child in self.process.children(recursive=True):
                try:
                    children_rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        self.peak_parent_rss = max(self.peak_parent_rss, parent_rss)
        self.peak_children_rss = max(self.peak_children_rss, children_rss)
        self.peak_process_tree_rss = max(self.peak_process_tree_rss, parent_rss + children_rss)


def _add_image_to_digest(hasher: Any, image: RenderData) -> None:
    """Hash one rendered image with explicit field lengths.

    Length-prefixing prevents adjacent fields from producing an ambiguous
    concatenation in the stable output digest.

    :param hasher:
        Incremental digest object.
    :param image:
        Rendered vault image and its output metadata.
    :return:
        None.
    """
    vault_id = str(image["vault_id"]).encode("utf-8")
    extension = str(image["extension"]).encode("ascii")
    payload = image["payload"]
    for value in (vault_id, extension, payload):
        hasher.update(struct.pack(">Q", len(value)))
        hasher.update(value)


def _render_sample(
    prepared: list[tuple[str, SparklineData]],
    *,
    render_workers: int,
    backend: str,
    batch_size: int,
) -> tuple[float, float, int, int, int, str]:
    """Render a prepared sample in bounded batches and discard each result.

    Timing and payload-size totals are accumulated while each batch is in
    memory, and the ordered output digest allows repeatability checks.

    :param prepared:
        Vault IDs paired with prepared chart data.
    :param render_workers:
        Number of render workers to use.
    :param backend:
        Joblib preference, either ``threads`` or ``processes``.
    :param batch_size:
        Maximum vaults retained in one render batch.
    :return:
        Render seconds, compression seconds, image count, uncompressed bytes,
        compressed bytes and ordered payload digest.
    """
    render_seconds = 0.0
    compression_seconds = 0.0
    image_count = 0
    uncompressed_bytes = 0
    compressed_bytes = 0
    hasher = hashlib.sha256()
    for offset in range(0, len(prepared), batch_size):
        batch = prepared[offset : offset + batch_size]
        started = time.perf_counter()
        rendered = render_sparklines(batch, max_workers=render_workers, backend=backend)
        render_seconds += time.perf_counter() - started
        compression_started = time.perf_counter()
        for image in rendered:
            _add_image_to_digest(hasher, image)
            payload = image["payload"]
            compressed_bytes += len(gzip.compress(payload, mtime=0))
            uncompressed_bytes += len(payload)
        compression_seconds += time.perf_counter() - compression_started
        image_count += len(rendered)
    return render_seconds, compression_seconds, image_count, uncompressed_bytes, compressed_bytes, hasher.hexdigest()


def main() -> None:  # noqa: PLR0914
    """Run the no-upload benchmark against configured pipeline files.

    The smoke run is deterministic; an optional full run uses a temporary
    state file and exercises the coordinator without creating an R2 client.

    :return:
        None.
    """
    setup_console_logging(only_log_file=False)
    data_dir = get_pipeline_data_dir()
    prices_path = Path(os.environ.get("SPARKLINE_PRICE_PATH", data_dir / "crypto-vaults" / "crypto-cleaned-vault-prices-1d.parquet"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", data_dir / "vault-metadata-db.pickle"))
    sample_setting = os.environ.get("SPARKLINE_BENCHMARK_SAMPLE_SIZE", "100")
    sample_size = None if sample_setting.lower() == "all" else int(sample_setting)
    repeat_count = int(os.environ.get("SPARKLINE_BENCHMARK_REPEATS", "3"))
    batch_size = int(os.environ.get("SPARKLINE_BATCH_SIZE", str(SPARKLINE_BATCH_SIZE)))
    render_workers = int(os.environ.get("SPARKLINE_RENDER_WORKERS") or "6")
    backend = os.environ.get("SPARKLINE_RENDER_BACKEND", "processes")
    if sample_size is not None and sample_size < 1:
        message = "SPARKLINE_BENCHMARK_SAMPLE_SIZE must be positive or 'all'"
        raise ValueError(message)
    if repeat_count < 1 or batch_size < 1 or render_workers < 1:
        message = "Sparkline benchmark repeats, batch size and render workers must be positive"
        raise ValueError(message)
    if backend not in {"processes", "threads"}:
        raise ValueError(f"Unsupported sparkline render backend: {backend!r}")

    prepare_started = time.perf_counter()
    prices_df = load_sparkline_price_data(prices_path)
    vault_db = VaultDatabase.read(vault_db_path)
    included_ids = get_included_vault_ids(vault_db, prices_df)
    selected_ids = sorted(included_ids)
    if sample_size is not None:
        selected_ids = selected_ids[:sample_size]
    prepared, skipped = prepare_vault_sparklines(prices_df, set(selected_ids))
    prepared.sort(key=lambda item: item[0])
    preparation_seconds = time.perf_counter() - prepare_started

    measurements: list[tuple[float, float, int, int, int, str]] = []
    with ProcessTreeMemorySampler() as memory:
        warmup = _render_sample(prepared, render_workers=render_workers, backend=backend, batch_size=batch_size)
        for _ in range(repeat_count):
            result = _render_sample(prepared, render_workers=render_workers, backend=backend, batch_size=batch_size)
            if result[5] != warmup[5]:
                message = "Sparkline output changed between benchmark repetitions"
                raise AssertionError(message)
            measurements.append(result)

    median_render = sorted(result[0] for result in measurements)[len(measurements) // 2]
    image_count = measurements[-1][2]
    report = [
        ["eligible vaults", len(included_ids)],
        ["sampled vaults", len(selected_ids)],
        ["prepared vaults", len(prepared)],
        ["insufficient history", skipped],
        ["backend / render workers", f"{backend} / {render_workers}"],
        ["batch size / measured repeats", f"{batch_size} / {repeat_count}"],
        ["preparation seconds", f"{preparation_seconds:.3f}"],
        ["median render seconds", f"{median_render:.3f}"],
        ["median vaults per second", f"{len(prepared) / median_render:.1f}" if median_render else "n/a"],
        ["compression seconds", f"{measurements[-1][1]:.3f}"],
        ["rendered images", image_count],
        ["uncompressed bytes", measurements[-1][3]],
        ["compressed bytes", measurements[-1][4]],
        ["output digest", measurements[-1][5]],
        ["peak parent RSS GiB", f"{memory.peak_parent_rss / 1024**3:.3f}"],
        ["peak children RSS GiB", f"{memory.peak_children_rss / 1024**3:.3f}"],
        ["peak process-tree RSS GiB", f"{memory.peak_process_tree_rss / 1024**3:.3f}"],
    ]
    print(tabulate(report, headers=["Metric", "Value"], tablefmt="github"))

    del prices_df, vault_db, prepared

    if os.environ.get("SPARKLINE_BENCHMARK_FULL_EXPORT", "false").lower() == "true":
        with tempfile.TemporaryDirectory(prefix="sparkline-benchmark-") as scratch_dir:
            state_path = Path(scratch_dir) / "sparkline-export-state.json"
            started = time.perf_counter()
            with ProcessTreeMemorySampler() as full_memory:
                result = run_sparkline_export(
                    data_dir=data_dir,
                    vault_db_path=vault_db_path,
                    prices_path=prices_path,
                    state_path=state_path,
                    render_workers=render_workers,
                    upload_workers=int(os.environ.get("SPARKLINE_UPLOAD_WORKERS") or os.environ.get("SPARKLINE_MAX_WORKERS") or "8"),
                    render_backend=backend,
                    force=True,
                    dry_run=True,
                )
            elapsed = time.perf_counter() - started
        full_report = [
            ["forced dry-run seconds", f"{elapsed:.3f}"],
            ["rendered / failed vaults", f"{result.counters['rendered']} / {result.counters['failed']}"],
            ["batches", result.counters["batches"]],
            ["peak process-tree RSS GiB", f"{full_memory.peak_process_tree_rss / 1024**3:.3f}"],
            ["scratch state only", "yes"],
        ]
        print(tabulate(full_report, headers=["Full coordinator", "Value"], tablefmt="github"))
        if not result.success:
            raise RuntimeError(f"Full sparkline benchmark failed for {result.counters['failed']} vaults")


if __name__ == "__main__":
    main()
