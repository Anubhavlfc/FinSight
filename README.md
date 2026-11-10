# FinSight: Financial Analysis Copilot

FinSight analyses how a US-listed company performed over its last three completed fiscal
years, using SEC filings. Every number traces back to a filing accession, a XBRL tag and
a formula.

**Status: Stage 1 (reliable financial data pipeline).** Company resolution, SEC
collection, normalization, validation, deterministic metrics, ranked changes, SQLite
persistence with resume, a CLI, and an offline demo. LLM investigation and reports come
in Stage 2, and the Streamlit dashboard in Stage 3. See [docs/architecture.md](docs/architecture.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Offline demo: bundled SYNTHETIC fixture, no network, no API keys
finsight analyze DEMO --offline --cutoff 2026-11-01

# Live SEC data. SEC requires automated clients to identify themselves.
export FINSIGHT_SEC_USER_AGENT="FinSight research your-name@your-domain.com"
finsight analyze MSFT
finsight analyze MSFT --cutoff 2025-12-31            # only filings available by that date
finsight analyze "Microsoft" --latest-fy 2025        # name lookup; window ends with FY2025
```

Each run prints the fiscal periods (with the 10-K that defines each one), a financial
table, year-over-year changes, flagged changes and validation issues. It also writes these
files to `out/<run_id>/`:

| File | Contents |
|---|---|
| `financial_dataset.csv` | Accepted values per field and fiscal year, full precision, USD |
| `metrics.csv` | Every metric with formula, status, note and input fact / metric IDs |
| `facts_audit_trail.csv` | Accepted, alternative (e.g. pre-restatement) and rejected candidates, each with a reason |
| `validation_issues.csv` | Issues by severity |
| `analysis.json` | Plan, sources (URL, retrieval time, SHA-256), validation report, metrics, flags |
| `run_summary.json` | State history, stats (coverage, cache hits, duration) |

Other commands:

```bash
finsight runs                                 # recent runs
finsight show <run_id> [--export]
finsight resume <run_id>                      # continue an interrupted or failed run
finsight resume <run_id> --company 9999901    # answer a "which company?" question
finsight approve <run_id> --note "checked"    # READY_FOR_REVIEW -> APPROVED (always explicit)
```

Exit codes: `0` ready for review / partial / approved, `2` failed, `3` needs input.

## Configuration

| Where | What |
|---|---|
| `FINSIGHT_SEC_USER_AGENT` | **Required for live runs.** Your name/app and a contact address. Never logged or committed. |
| `FINSIGHT_HOME` | Base folder for `.finsight/` cache + database and `out/` (default: current directory) |
| `FINSIGHT_CONFIG_DIR` | Alternative config folder |
| `config/settings.toml` | Request pacing, retries, backoff, period rules, tolerances, change thresholds, unsupported industries |
| `config/mappings.toml` | Standardized field → ordered XBRL tags, sign rules, derivations |
| `config/companies/<CIK>.toml` | Per-company tag overrides |

The change thresholds (revenue ±10%, operating margin ±3 pp, free cash flow ±20%, debt
+25%, materiality 0.5% of revenue) are investigation settings, not financial standards.

## Scope

Supported: US domestic, nonfinancial operating companies that file Form 10-K in USD.
Banks, broker-dealers, insurers, REITs and other SIC 6000–6799 filers, and foreign
private issuers (20-F/40-F), are refused with an explanation rather than given ratios
that do not fit them.

## Tests

```bash
pytest                                   # unit + offline end-to-end tests (no network)
FINSIGHT_RUN_LIVE=1 FINSIGHT_SEC_USER_AGENT="..." pytest -m live   # real SEC calls, opt-in
```

The offline tests check the demo pipeline against values worked out by hand from
`scripts/build_demo_fixture.py`. They also cover restatements, a 10-K/A, a quarterly value
labelled as fiscal-year, duplicates and competing tags, sign handling, missing data,
balance-check failures, zero and negative denominators, retry and pacing behaviour, cache
reuse, bounded recovery, and resuming after a simulated crash.

## Project layout

```
finsight/
  contracts.py     typed schemas (AnalysisRequest … WorkflowRun)
  config.py        settings + field mappings loader
  sec/client.py    SEC HTTP client: identification, pacing, retries
  sec/sources.py   live source with cache, offline fixture source
  resolver.py      ticker / CIK / name → company, scope checks
  planner.py       fiscal-period selection → AnalysisPlan
  collector.py     company facts → candidate rows
  normalizer.py    period, restatement, tag, sign, derivation rules
  validator.py     accounting checks, coverage → ValidationReport
  metrics.py       deterministic metrics with lineage
  changes.py       rule-based change ranking
  workflow.py      state machine
  orchestrator.py  stage routing, recovery, checkpoints
  storage.py       SQLite
  export.py        CSV / JSON exports and display tables
  cli.py
config/            settings.toml, mappings.toml, companies/
fixtures/demo/     SYNTHETIC offline fixture (labelled)
scripts/           fixture generator
tests/unit/        offline tests
tests/integration/ live SEC tests (opt-in)
docs/              architecture and data policies
```

## Roadmap

1. **Stage 1 (this release):** data pipeline, CLI, offline demo.
2. **Stage 2:** filing-text retrieval, investigator with typed tools and step budgets,
   evidence-backed findings, report (Markdown/HTML) with charts and citations, reviewer
   with at most two revision cycles, Anthropic SDK behind a provider interface (model from
   an environment variable), and a single-prompt baseline for comparison.
3. **Stage 3:** Streamlit dashboard, downloads, scheduled check for new 10-K filings.
4. **Stage 4:** uploaded reports, industry profiles, peers.
