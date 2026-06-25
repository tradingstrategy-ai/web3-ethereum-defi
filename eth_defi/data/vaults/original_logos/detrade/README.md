# DeTrade logo

## Source

- Website: https://detrade.fund/
- Official square mark: https://detrade.fund/favicon.png
- Official header wordmark: https://detrade.fund/detrade-logo-text.png
- X: https://x.com/DeTradefund

## Files

- `detrade.generic.png` - square DeTrade mark from the official website favicon.
- `detrade.light.png` - DeTrade header wordmark from the official website navigation.

## Processing

The formatted logo was generated with:

```shell
INPUT_IMAGE=eth_defi/data/vaults/original_logos/detrade/detrade.generic.png OUTPUT_IMAGE=eth_defi/data/vaults/formatted_logos/detrade/generic.png poetry run python scripts/logos/post-process-logo.py
```

The square mark was selected for formatted output because it is already a 1:1 brand mark. The header wordmark is retained as an original source variant but is less suitable for the 256x256 square icon.
