# TiD Capital logo sources

The official [@tidcapital Twitter/X profile](https://x.com/tidcapital) uses a
378×378 JPEG containing the blue-and-white **TID monogram**.  This is the
organisation's compact brandmark (rather than a wordmark): it has no expanded
company name and remains legible at favicon-sized dimensions.  The source was
fetched on 2026-07-30 and is stored as `tid-capital.generic.jpg`.

The source image was run through
[`scripts/logos/post-process-logo.py`](../../../../../scripts/logos/post-process-logo.py)
to remove its black background and produce transparent 256×256 PNGs in the
formatted-logo directory.  The same official colourway is suitable on both
light and dark UI surfaces, so `generic.png`, `light.png`, and `dark.png` all
use this brandmark.
