# Flowdesk logo sources

Flowdesk's existing asset was a wide wordmark. The compact Flowdesk symbol from
the official favicon assets is used instead, so the curator artwork remains
recognisable at small sizes.

## Official sources

- Homepage: <https://www.flowdesk.co>
- 256px favicon (white symbol on black):
  <https://cdn.prod.website-files.com/69807514ef19ea1c45a6ec49/69cb9d0d5b13bf0c45596fab_fav256.png>
- Light-theme 32px favicon (white symbol):
  <https://cdn.prod.website-files.com/69807514ef19ea1c45a6ec49/69cb9cd41cd70960a8df154a_fav32_light.png>
- Dark-theme 32px favicon (black symbol):
  <https://cdn.prod.website-files.com/69807514ef19ea1c45a6ec49/69cb9d0684b263afa6062e1d_fav32_dark.png>
- Existing full wordmark references (retained as provenance):
  <https://cdn.prod.website-files.com/69807514ef19ea1c45a6ec49/69820a8102499f1c840838c5_flowdesk_logo_black.svg>
  and
  <https://cdn.prod.website-files.com/69807514ef19ea1c45a6ec49/69820a81f7303c1df0e976c9_flowdesk_logo_white.svg>

## Files and processing

- `flowdesk.fav256.png`, `flowdesk.fav32_light.png`, and
  `flowdesk.fav32_dark.png` are the downloaded official favicon sources.
- `flowdesk.generic.png`, `flowdesk.light.png`, and `flowdesk.dark.png` are
  transparent brandmark sources. The solid favicon backgrounds were removed
  by luminance masking while retaining the antialiased symbol edges.
- The three 256×256 transparent outputs in
  `eth_defi/data/vaults/formatted_logos/flowdesk/` were generated with
  `scripts/logos/post-process-logo.py` on 2026-07-30. `generic.png` and
  `light.png` contain the white mark; `dark.png` is the RGB-inverted black
  mark for light surfaces.

