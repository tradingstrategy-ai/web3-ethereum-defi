#!/usr/bin/env python3

"""Compatibility wrapper for the top-vaults JSON exporter.

The implementation lives in :mod:`eth_defi.vault.top_vaults_json` so package
code can import it normally. Keep this script so existing operator commands
continue to work.
"""

from eth_defi.vault.top_vaults_json import main


if __name__ == "__main__":
    main()
