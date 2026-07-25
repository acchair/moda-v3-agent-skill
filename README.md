# Install moda-v3

## Give your Agent a disciplined A-share research workflow

Choose your Agent. Copy one prompt. Your Agent does the rest.

Five-factor research | Source-labeled evidence | Conservative hard caps

## Set up your Agent

### One-liner for your Agent

Copy this prompt into an Agent with access to this private repository:

```text
Install the moda-v3 skill from https://github.com/acchair/moda-v3-agent-skill into your configured skills directory. Install requirements.txt, read SKILL.md, and preserve the repository-relative tools and knowledge directories.
```

### Platform compatibility

| Platform | Skill folder | Status |
|---|---|---|
| Codex | `SKILL.md` + `agents/openai.yaml` | Recommended |
| Any Agent that loads `SKILL.md` | Copy this repository to its skills directory | Supported |

## What your Agent gets

- A five-factor A-share analysis framework covering industry, shareholders, survival, profit realization, and valuation/reversal.
- Local data-collection and technical-analysis scripts with source-labeled evidence.
- Conservative ratings: `根`, `矛`, `学习仓`, or `不碰`; missing data stays `需人工确认`.

## Run a research job

```powershell
python -m pip install -r requirements.txt
python tools/run_pipeline.py --stock 000001 --name 平安银行
```

Reports are written to `knowledge/research/`. Export the final Agent response with `tools/export_skill_output.py`; set `MODA_OUTPUT_DIR` to change its output directory.

## Private by design

This repository does not include browser binaries, login state, Cookies, local logs, historical reports, the web workbench, or the industry-chain database. Optional proxy credentials are read only from environment variables and must never be committed.
