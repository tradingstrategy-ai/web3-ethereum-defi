# Cassa logo

The compact Cassa brandmark is the three-sided prism shown beside the Cassa
name in the official beta application header and footer. It is an inline SVG,
not a wordmark, and is suitable for the small square vault-logo display.

Source page: <https://beta.cassa.fyi/> (inline `80×109` SVG brandmark in the
header/footer application bundle).

Retrieved on 2026-07-30. The original brandmark uses Cassa's official white
and slate colours on a transparent background:

- `cassa.brandmark.svg` — official white/slate brandmark used as the generic
  and light (dark-background) source.
- `cassa.light.svg` — identical official light-background source.
- `cassa.dark.svg` — dark recolour of the same official geometry for light
  backgrounds; no alternate dark asset is published by Cassa.

The earlier `cassa.generic.svg` navigation wordmark is retained as a source
reference, but is no longer used for the processed vault logo.

## Processing

All three variants were processed with
[`scripts/logos/post-process-logo.py`](../../../../scripts/logos/post-process-logo.py)
into transparent 256×256 PNGs:

- `formatted_logos/cassa/generic.png`
- `formatted_logos/cassa/light.png`
- `formatted_logos/cassa/dark.png`
