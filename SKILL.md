---
name: moda-v3
description: Analyze mainland China A-share stocks with the moda-v3 five-factor framework, local data-collection scripts, source-labeled evidence, hard caps, and disciplined ratings. Use when the user supplies an A-share code or asks to analyze an A-share company, valuation, industry position, shareholder risk, technical setup, or a research decision.
---

# Moda V3 A-share Analysis

## Run

1. Resolve a supplied stock name with the bundled chain database first: `python -c "from tools.webapp.chain_db import search; print(search('{name}', 5))"`. Use external search only when the local result is empty; if uncertain, state the ambiguity instead of guessing.
2. From this skill directory, run:

```powershell
python tools/run_pipeline.py --stock {code} --name {name}
```

3. Read `knowledge/research/pipeline/{code}.json`, then read only reports whose current-run module status is `ok: true`. If a source fails, mark the relevant conclusion `需人工确认`; never read an older report as a fallback.
4. Only when the user requests a file or reusable artifact, export the exact final response with:

```powershell
python tools/export_skill_output.py --stock {code} --name {name}
```

Pass the response through standard input or `--input`. The default output directory is `knowledge/output/`; set `MODA_OUTPUT_DIR` to override it.

## Output Rules

- Start with total score, rating, and signal. Ratings are only `根`、`矛`、`学习仓`、`不碰`.
- Use five factors: F1 industry trend and capital expenditure, F2 shareholders and positioning, F3 survival and leadership, F4 profit realization, F5 valuation and reversal.
- Label each key conclusion with its actual source, such as `[easy_tdx/TDX]`, `[easy_tdx/Sina]`, `[a-stock-data]`, `[AKShare]`, or `[TDX]`.
- The scorer reports module coverage and factor coverage; partial coverage remains `需人工确认`, never a positive conclusion.
- Never turn missing data into a positive conclusion. State `需人工确认`.
- Treat keyword scores as a baseline only; honor structured financial caps when revenue, profit, cash flow, leverage, or relative valuation contradict the keyword evidence.
- Apply hard caps: controlling-shareholder reduction caps at `学习仓`; ST or delisting risk is `不碰`; F1 below 15 or F3 below 8 caps at `学习仓`.
- End with falsification triggers covering industry, company, valuation, shareholder, and relative-value changes.
- This skill provides research only, not investment advice.

## Limits

- Browser-based sentiment, login state, web workbench, and industry-chain database are intentionally excluded.
- Overseas revenue and browser-only evidence remain `需人工确认`.
- AxData is an optional enrichment adapter, disabled by default. Set `MODA_AXDATA=1` only for explicit validation; prefer bundled easy_tdx adapters for overlapping TDX/Sina capabilities.
- Optional proxy credentials are read only from environment variables. Do not print, store, or add them to files.
