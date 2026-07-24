"""Pytest configuration for the vault protocol characterisation tests.

Automatically tags every test collected under this directory
(``tests/erc_4626/vault_protocol/``) with the ``vault_characterisation`` marker
so the whole group can be selected or deselected locally with
``-m vault_characterisation`` / ``-m 'not vault_characterisation'``.

In CI this group is gated by ``.github/workflows/test-vault-protocol.yml`` and
excluded from the main run in ``.github/workflows/test.yml`` via ``--ignore``, so
no per-file marker maintenance is required here.
"""

import pathlib

import pytest

#: Directory this conftest lives in; only tests below it get the marker.
_THIS_DIR = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply ``vault_characterisation`` to tests collected under this directory.

    ``pytest_collection_modifyitems`` is a session-wide hook and receives every
    collected item — including tests from other directories when the whole suite
    runs — so we filter to items whose path is under this conftest's directory
    before marking them. This avoids mislabelling unrelated tests.

    :param items:
        All collected test items for the session, mutated in place.
    """
    for item in items:
        item_path = pathlib.Path(str(item.fspath))
        if _THIS_DIR == item_path.parent or _THIS_DIR in item_path.parents:
            item.add_marker(pytest.mark.vault_characterisation)
