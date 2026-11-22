# Google Sheets integration tests

The [vault review sync tests](../../tests/hyperliquid/test_vault_review_sync.py) write to a Google worksheet. Use a dedicated spreadsheet or worksheet so the tests cannot overwrite a production review.

1. Enable the Google Sheets API in the test Google Cloud project. Create a service account and a JSON key.
2. Open the test spreadsheet and share it with the service account email as **Editor**. In the Trading Strategy Workspace, browser automation failed to grant access during setup on 11 April 2026. Sharing from the owner's browser worked.
3. Set the following variables in the private environment sourced by the repository's `.local-test.env`.

| Variable | Value |
| --- | --- |
| `TEST_GS_SERVICE_ACCOUNT_JSON` | The service account key as a single-line JSON string. Keep the key outside the repository. |
| `TEST_GS_SHEET_URL` | The full URL of the test spreadsheet. |
| `TEST_GS_WORKSHEET_NAME` | The exact name of the dedicated test tab. |

Install the optional `gsheets` and `test` extras in the Poetry environment, then run the module.

```shell
source .local-test.env && timeout 180s poetry run pytest tests/hyperliquid/test_vault_review_sync.py
```

The module skips the live tests when gspread or the test settings are missing. For the production sync settings, see the [Hyperliquid vault pipeline](../../scripts/hyperliquid/README-hyperliquid-vaults.md#setting-up-the-sheet-and-service-account).
