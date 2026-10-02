"""Exact displayed chart entity evidence for subsequent social campaigns.

This small report artefact preserves selection provenance without changing the
historical price schema. Ghost's served image may differ from the local PNG.
"""

import hashlib
import json
from pathlib import Path

import pandas as pd
import requests


def chart_records(frame: pd.DataFrame, kind: str, metric: str, visible_ids: list[str] | None = None) -> list[dict]:
    """Export stable identities and the metric from a chart's final selection.

    Performance/scatter figures expose visible ids to exclude missing price
    series and points removed by fitted axes. Other frames are final aggregates.

    :param frame: Final selected rows; vault rows include names/slugs and metrics.
    :param kind: vault, protocol, chain or fund.
    :param metric: Actual ranking column.
    :param visible_ids: Optional ids actually drawn by the figure.
    :return: JSON-compatible displayed records, preserving selection order.
    """
    if visible_ids is not None:
        frame = frame.loc[frame.index.intersection(visible_ids, sort=False)]
    records = []
    for position, (entity_id, row) in enumerate(frame.iterrows(), 1):
        item = {"entity_id": str(entity_id), "kind": kind, "rank": position}
        for column in ("name", "curator_slug", "curator_name", "manager_slug", "protocol_slug", "chain", "chain_id", metric):
            value = row.get(column)
            if value is not None and not isinstance(value, (list, dict)) and not pd.isna(value):
                if hasattr(value, "item"):
                    value = value.item()
                item[column] = value
        records.append(item)
    return records


def write_chart_metadata(output_dir: Path, charts: dict[str, Path], selections: dict[str, tuple[pd.DataFrame, str, str]], figures: dict) -> None:
    """Write evidence against the exact rendered image and final visible rows.

    It is separate from report.json so existing consumers remain compatible.

    :param output_dir: Report bundle.
    :param charts: Rendered chart paths.
    :param selections: Chart key to final frame, entity kind and ranking metric.
    :param figures: Chart key to Plotly figure/panel tuple.
    """
    evidence = {}
    for key, path in charts.items():
        frame, kind, metric = selections[key]
        meta = figures[key][0].layout.meta or {}
        evidence[key] = {"original_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "metric": metric, "entities": chart_records(frame, kind, metric, meta.get("vault_ids")), "period": "report chart selection", "filters": "Final chart selection, including investability, chart risk and visibility filters"}
    (output_dir / "chart-metadata.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")


def fetch_bind_served_images(output_dir: Path, chart_urls: dict[str, str]) -> None:
    """Bind Ghost-uploaded versions to the local exact selection evidence.

    Server optimisation changes bytes, so the served hash is recorded separately
    from the original renderer hash. A failed read-back aborts publication.

    :param output_dir: Report bundle containing chart metadata.
    :param chart_urls: Exact URLs returned by Ghost image uploads.
    """
    path = output_dir / "chart-metadata.json"
    if not path.exists():
        return
    evidence = json.loads(path.read_text())
    for key, url in chart_urls.items():
        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as exc:
            raise ValueError(f"Ghost served-image read-back failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise ValueError(f"Ghost served-image read-back failed: HTTP {response.status_code}")
        evidence[key].update(url=url, sha256=hashlib.sha256(response.content).hexdigest())
    path.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
