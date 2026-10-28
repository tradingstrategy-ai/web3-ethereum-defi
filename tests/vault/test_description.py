"""Single-line vault descriptions shared by adapters and scanner exports."""

import pytest

from eth_defi.vault.base import normalise_vault_description, normalise_vault_short_description


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", ""),
        (" \t\r\n ", ""),
        ("Unchanged description.", "Unchanged description."),
        ("First.\n\nSecond.", "First. Second."),
        ("First.\r\nSecond.\rThird.", "First. Second. Third."),
        (r"First.\nSecond.\r\nThird.", "First. Second. Third."),
        ("  First.\\n\tSecond.\r\nThird.  ", "First. Second. Third."),
        ("Issuer\u2019s [strategy](https://example.com/vault).\n£100.", "Issuer\u2019s [strategy](https://example.com/vault). £100."),
        (r"Keep \u0041 and \t escapes.", r"Keep \u0041 and \t escapes."),
    ],
)
def test_normalise_vault_description(text: str | None, expected: str | None) -> None:
    """Clean source line breaks while preserving text and missing values.

    Repeated normalisation must be stable across cached and newly fetched text.

    :param text:
        Raw optional description.
    :param expected:
        Expected single-line description.
    """
    assert normalise_vault_description(text) == expected
    assert normalise_vault_description(normalise_vault_description(text)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", ""),
        (" \t\r\n ", ""),
        ("One sentence without punctuation", "One sentence without punctuation"),
        ("First sentence. Second sentence.", "First sentence."),
        ("First sentence? Second sentence!", "First sentence?"),
        ("First sentence! Second sentence.", "First sentence!"),
        ("First sentence.\n\nSecond sentence.", "First sentence."),
        (r"First sentence.\r\nSecond sentence.", "First sentence."),
        ("Earn 4.5% via example.com markets. More details.", "Earn 4.5% via example.com markets."),
        ("Tokenised U.S. Treasury-bill investment product with USD NAV.", "Tokenised U.S. Treasury-bill investment product with USD NAV."),
        ("U.S. Treasury bills, bonds and overnight repurchase-agreement strategy", "U.S. Treasury bills, bonds and overnight repurchase-agreement strategy"),
        ("J.P. Morgan manages U.S. Treasuries. More details.", "J.P. Morgan manages U.S. Treasuries."),
        ("Lend assets, e.g. USDC. More details.", "Lend assets, e.g. USDC."),
        ("See [the vault](https://example.com/vault). More details.", "See [the vault](https://example.com/vault)."),
    ],
)
def test_normalise_vault_short_description(text: str | None, expected: str | None) -> None:
    """Keep only the first sentence after flattening the source text.

    Both supplied summaries and full descriptions copied as summaries use the
    same rule, without splitting decimal numbers or links at internal dots.

    :param text:
        Raw optional summary or copied description.
    :param expected:
        Expected single-line first sentence.
    """
    assert normalise_vault_short_description(text) == expected
    assert normalise_vault_short_description(normalise_vault_short_description(text)) == expected
