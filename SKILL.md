---
name: moda-v3
description: Analyze mainland China A-share stocks with the moda-v3 five-factor framework, local data-collection scripts, source-labeled evidence, hard caps, and disciplined ratings. Use when the user supplies an A-share code or asks to analyze an A-share company, valuation, industry position, shareholder risk, technical setup, or a research decision.
---

# Moda V3 A-share Analysis

## Run

1. Resolve a supplied stock name to a six-digit A-share code. If uncertain, state the ambiguity instead of guessing.
2. From this skill directory, run:

```powershell
python tools/run_pipeline.py --stock {code} --name {name}
```

3. Read the fresh reports under `knowledge/research/` and produce the final analysis. If a source fails, retain the report's failure state and mark the relevant conclusion `需人工确认`.
4. Export the exact final response with:

```powershell
python tools/export_skill_output.py --stock {code} --name {name}
```

Pass the response through standard input or `--input`. The default output directory is `knowledge/output/`; set `MODA_OUTPUT_DIR` to override it.

## Output Rules

- Start with total score, rating, and signal. Ratings are only `根`、`矛`、`学习仓`、`不碰`.
- Use five factors: F1 industry trend and capital expenditure, F2 shareholders and positioning, F3 survival and leadership, F4 profit realization, F5 valuation and reversal.
- Label each key conclusion with its actual source, such as `[easy_tdx]`, `[a-stock-data]`, `[AKShare]`, `[BaoStock]`, or `[TDX]`.
- Never turn missing data into a positive conclusion. State `需人工确认`.
- Apply hard caps: controlling-shareholder reduction caps at `学习仓`; ST or delisting risk is `不碰`; F1 below 15 or F3 below 8 caps at `学习仓`.
- End with falsification triggers covering industry, company, valuation, shareholder, and relative-value changes.
- This skill provides research only, not investment advice.

## Limits

- Browser-based sentiment, login state, web workbench, and industry-chain database are intentionally excluded.
- Overseas revenue and browser-only evidence remain `需人工确认`.
- Optional proxy credentials are read only from environment variables. Do not print, store, or add them to files.
