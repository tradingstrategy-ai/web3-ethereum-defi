"""Adapter protocol and reproducible capability declarations for perp vaults."""

import hashlib
import json
from dataclasses import asdict, dataclass

import pyarrow as pa


@dataclass(slots=True, frozen=True)
class PerpDexCapability:
    """Protocol facts needed by generic storage and price joining.

    The capability is data, not a protocol-specific cleaning branch.  It is
    embedded in Parquet metadata to make re-cleaning deterministic.
    """

    protocol_slug: str
    deployment_slug: str
    quote_asset: str
    public_positions: bool
    position_data_status: str
    collection_cadence_seconds: int
    maximum_position_valuation_skew_seconds: int


@dataclass(slots=True, frozen=True)
class PerpDexCapabilityRegistry:
    """Canonical immutable set of capabilities used for one raw data artefact."""

    capabilities: tuple[PerpDexCapability, ...]
    schema_version: int = 1

    def to_json(self) -> str:
        """Return deterministic JSON suitable for Parquet schema metadata."""
        records = sorted((asdict(capability) for capability in self.capabilities), key=lambda item: (item["protocol_slug"], item["deployment_slug"]))
        return json.dumps({"schema_version": self.schema_version, "capabilities": records}, sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        """Return the deterministic registry JSON digest."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


PERP_CAPABILITY_REGISTRY_METADATA_KEY = b"perp_dex.capability_registry"
PERP_CAPABILITY_REGISTRY_HASH_METADATA_KEY = b"perp_dex.capability_registry_sha256"


def embed_perp_capability_registry(schema: pa.Schema, registry: PerpDexCapabilityRegistry) -> pa.Schema:
    """Add the canonical registry and its integrity hash to Parquet metadata.

    :param schema:
        Existing Arrow schema.
    :param registry:
        Capabilities used to construct the raw artefact.
    :return:
        Schema with preserved and extended metadata.
    """
    metadata = dict(schema.metadata or {})
    metadata[PERP_CAPABILITY_REGISTRY_METADATA_KEY] = registry.to_json().encode("utf-8")
    metadata[PERP_CAPABILITY_REGISTRY_HASH_METADATA_KEY] = registry.sha256().encode("ascii")
    return schema.with_metadata(metadata)


def load_perp_capability_registry(schema: pa.Schema) -> PerpDexCapabilityRegistry:
    """Read and verify the embedded capability registry from Parquet metadata.

    :param schema:
        Raw or cleaned Parquet schema.
    :return:
        Verified capability registry.
    :raises ValueError:
        If metadata is absent, malformed or has an invalid hash.
    """
    metadata = schema.metadata or {}
    raw = metadata.get(PERP_CAPABILITY_REGISTRY_METADATA_KEY)
    declared_hash = metadata.get(PERP_CAPABILITY_REGISTRY_HASH_METADATA_KEY)
    if raw is None or declared_hash is None:
        msg = "Perp capability registry metadata is missing; run the explicit raw-Parquet migration"
        raise ValueError(msg)
    actual_hash = hashlib.sha256(raw).hexdigest().encode("ascii")
    if actual_hash != declared_hash:
        msg = "Perp capability registry metadata hash does not match its contents"
        raise ValueError(msg)
    decoded = json.loads(raw)
    capabilities = tuple(PerpDexCapability(**record) for record in decoded["capabilities"])
    return PerpDexCapabilityRegistry(capabilities=capabilities, schema_version=int(decoded["schema_version"]))
