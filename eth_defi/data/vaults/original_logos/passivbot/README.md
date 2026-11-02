# PassivBot logo

The [official project repository](https://github.com/enarjord/passivbot) supplies
the compact brand mark as `docs/images/pbot_logo_square.svg`.

- [Original SVG at the retrieved revision](https://raw.githubusercontent.com/enarjord/passivbot/f6ffee98c3abe22b321a9b14641bc3133d7855cd/docs/images/pbot_logo_square.svg)
- Retrieved: 2026-11-01
- Original: `passivbot.generic.svg`, 1,008 bytes, SVG view box 71 × 85
- Formatted: `../../formatted_logos/passivbot/generic.png`, 256 × 256 RGBA PNG

The upstream filename says square, but the view box is taller than it is wide.
The standard processor preserves the mark's proportions and centres it on a
transparent square canvas. Its teal and red colours are retained.

Reproduce the formatted logo with the repository's standard processor:

```shell
INPUT_IMAGE=eth_defi/data/vaults/original_logos/passivbot/passivbot.generic.svg OUTPUT_IMAGE=eth_defi/data/vaults/formatted_logos/passivbot/generic.png poetry run python scripts/logos/post-process-logo.py
```
