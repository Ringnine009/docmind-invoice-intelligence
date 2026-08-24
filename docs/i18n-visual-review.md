# i18n visual review (qwen-vl)

The dashboard UI was captured in **EN** and **ZH** (Edge headless, 1320×940,
demo batch of 30 synthetic invoices) and reviewed with `qwen-vl-plus` (see
`research/demo-test/visual_review.py` in the workspace). Screenshots and raw
reviews live outside this repo (`research/demo-test/`):
`docmind_analysis_{en,zh}.png`, `docmind_graph_{en,zh}.png`.

## Conclusions

| Shot | Layout | Language | Issues |
|---|---|---|---|
| Analysis · EN | clean, no overflow / cut-off / misalignment | English ✓ | minor: x-axis date labels could be spaced more with many points |
| Analysis · ZH | clean | Chinese ✓, no tofu | same minor note |
| Graph · EN | clean; legend clear (74 nodes / 137 edges) | English ✓ | dense areas may have some label overlap (inherent to force layouts) |
| Graph · ZH | clean | Chinese ✓, no tofu | node labels small on narrow screens (cosmetic) |

No functional or rendering defects found; no action items beyond optional
polish. Language toggle (EN / 中文, persisted in `localStorage`) switches all
UI chrome instantly; backend data keys and numeric values are not translated.
