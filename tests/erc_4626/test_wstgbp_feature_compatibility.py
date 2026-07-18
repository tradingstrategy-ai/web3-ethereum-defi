"""Compatibility tests for the wstGBP vault feature migration."""

import pickle

from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name


class LegacyMaseerFeature:
    """Represent the legacy enum payload stored in vault database pickles."""

    def __reduce__(self) -> tuple[object, tuple[str]]:
        """Recreate the old enum-value deserialisation call.

        :return:
            Enum constructor and the persisted legacy value.
        """

        return ERC4626Feature, ("maseer_one_like",)


def test_unpickle_legacy_maseer_feature_as_wstgbp() -> None:
    """Load legacy Maseer feature values from an existing vault database."""

    restored_feature = pickle.loads(pickle.dumps(LegacyMaseerFeature()))

    assert restored_feature is ERC4626Feature.wstgbp_like
    assert get_vault_protocol_name({restored_feature}) == "wstGBP"
