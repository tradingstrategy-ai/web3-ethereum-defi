"""Create a vault protocol slug from a protocol name.

Takes a protocol name (as returned by get_vault_protocol_name()) and converts it
to a URL-friendly slug format used in vault metadata and URLs.

Usage:

.. code-block:: shell

    # Basic usage
    python scripts/erc-4626/create-protocol-slug.py "Lagoon Finance"
    # Output: lagoon-finance

    # Or using environment variable
    PROTOCOL_NAME="Morpho" python scripts/erc-4626/create-protocol-slug.py
    # Output: morpho

"""

import os
import sys

from slugify import slugify


def slugify_protocol(protocol: str) -> str:
    """Create a slug from protocol name for URLs.

    Mirrors the implementation in eth_defi.research.vault_metrics.

    :param protocol:
        The protocol name from get_vault_protocol_name().

    :return:
        URL-friendly slug (lowercase, dashes instead of spaces).
    """
    if "unknown" in protocol.lower() or "not identifier" in protocol.lower():
        return "unknown"

    return slugify(protocol)


def main():
    # Get protocol name from command line argument or environment variable
    if len(sys.argv) > 1:
        protocol_name = " ".join(sys.argv[1:])
    else:
        protocol_name = os.environ.get("PROTOCOL_NAME")

    if not protocol_name:
        print("Usage: python scripts/erc-4626/create-protocol-slug.py <protocol name>", file=sys.stderr)
        print("   Or: PROTOCOL_NAME='...' python scripts/erc-4626/create-protocol-slug.py", file=sys.stderr)
        print("\nExamples:", file=sys.stderr)
        print("  python scripts/erc-4626/create-protocol-slug.py 'Lagoon Finance'", file=sys.stderr)
        print("  python scripts/erc-4626/create-protocol-slug.py Morpho", file=sys.stderr)
        sys.exit(1)

    slug = slugify_protocol(protocol_name)
    print(slug)


if __name__ == "__main__":
    main()
