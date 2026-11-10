# FinSight: Financial Analysis Copilot

FinSight analyses how a US-listed company performed over its last three completed fiscal
years, using SEC filings. Every number traces back to a filing accession, a XBRL tag and
a formula.

**Status: Stage 2 (evidence-backed investigation).** Stage 1's data pipeline (company
resolution, SEC collection, normalization, validation, deterministic metrics, ranked
changes, SQLite persistence with resume) now feeds an investigator that looks for the
company's own explanation of each flagged change in its 10-K, a report writer (Markdown and
HTML with charts and citations) and a reviewer that checks every number and quote. The
Streamlit dashboard comes in Stage 3. See [docs/architecture.md](docs/architecture.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Offline demo: bundled SYNTHETIC fixture, no network, no API keys
finsight analyze DEMO --offline --cutoff 2026-11-01

# Live SEC data. SEC requires automated clients to identify themselves.
export FINSIGHT_SEC_USER_AGENT="FinSight research your-name@your-domain.com"
finsight analyze MSFT                                # investigator: Claude if ANTHROPIC_API_KEY is set, else the stub
export ANTHROPIC_API_KEY=...                         # read by the Anthropic SDK; never stored or logged
export FINSIGHT_LLM_MODEL=claude-opus-5-5            # optional; default in config/settings.toml
finsight analyze MSFT --question "Why did margins change?"
finsight analyze MSFT --llm stub                     # force the rule-based offline stub (no LLM, no cost)
finsight analyze MSFT --no-investigate               # Stage 1 only
finsight analyze MSFT --cutoff 2025-12-31            # only filings available by that date
finsight analyze "Microsoft" --latest-fy 2025        # name lookup; window ends with FY2025
```

Each run prints the fiscal periods (with the 10-K that defines each one), a financial
table, year-over-year changes, flagged changes, each finding with its evidence status, the
review result and validation issues. It also writes these files to `out/<run_id>/`:

| File | Contents |
|---|---|
| `financial_dataset.csv` | Accepted values per field and fiscal year, full precision, USD |
| `metrics.csv` | Every metric with formula, status, note and input fact / metric IDs |
| `facts_audit_trail.csv` | Accepted, alternative (e.g. pre-restatement) and rejected candidates, each with a reason |
| `validation_issues.csv` | Issues by severity |
| `analysis.json` | Plan, sources (URL, retrieval time, SHA-256), validation report, metrics, flags |
| `run_summary.json` | State history, stats (coverage, cache hits, duration, LLM calls/tokens/estimated cost, review result) |
| `report.md`, `report.html` | The report: summary, table, charts, ranked changes, explanations, limitations, sources, calculation notes. The HTML is self-contained |
| `charts/*.png`, `charts/*.csv` | Four charts and the exact values plotted, with metric IDs |
| `findings.json` | Each finding: observation, management explanation, verified quotes, interpretation, unresolved questions, status, tool trace |
| `review.json` | Every review cycle: checks run, defects, where each was routed |
| `evidence_passages.csv` | Every cited filing passage with accession, section, character range and URL |

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
| `ANTHROPIC_API_KEY` | Enables the Claude investigator and LLM review. Read by the Anthropic SDK only. |
| `FINSIGHT_LLM_MODEL` | Claude model (default `claude-opus-5-5`). |
| `FINSIGHT_HOME` | Base folder for `.finsight/` cache + database and `out/` (default: current directory) |
| `FINSIGHT_CONFIG_DIR` | Alternative config folder |
| `config/settings.toml` | Request pacing, retries, backoff, period rules, tolerances, change thresholds, unsupported industries, investigation budgets (`[investigation]`), LLM settings and run budget (`[llm]`) |
| `config/mappings.toml` | Standardized field → ordered XBRL tags, sign rules, derivations |
| `config/companies/<CIK>.toml` | Per-company tag overrides |

The change thresholds (revenue ±10%, operating margin ±3 pp, free cash flow ±20%, net
income ±20%, capital expenditure ±25%, debt +25%, materiality 0.5% of revenue) are
investigation settings, not financial standards.

## How findings are evidenced

* The **calculated observation** is written by code from validated metrics.
* A **management explanation** is reported only if backed by quotes that code finds
  verbatim in the cited 10-K passage. It is always attributed to the company.
* An **analyst interpretation** is optional, labelled, and flagged by the reviewer if it
  states a cause without hedging.
* **Unresolved questions** say what the filings did not explain.
* Evidence status (supported / partially supported / unsupported) is set by code; the
  model can lower it, never raise it. Self-reported confidence is not used.

Without an API key (or with `--offline`) a **rule-based stub** stands in for Claude so the
whole workflow runs for free. It is labelled everywhere it is used and is deliberately
conservative: it only quotes sentences whose subject is the flagged item and that state a
cause, so on real filings it leaves many changes "unexplained" that Claude can explain.

## Scope

Supported: US domestic, nonfinancial operating companies that file Form 10-K in USD.
Banks, broker-dealers, insurers, REITs and other SIC 6000–6799 filers, and foreign
private issuers (20-F/40-F), are refused with an explanation rather than given ratios
that do not fit them.

## Tests

```bash
pytest                                   # unit + offline end-to-end tests (no network)
FINSIGHT_RUN_LIVE=1 FINSIGHT_SEC_USER_AGENT="..." pytest -m live   # real SEC calls, opt-in
FINSIGHT_RUN_LIVE=1 FINSIGHT_RUN_LLM=1 ANTHROPIC_API_KEY=... pytest -m llm   # real Claude calls, costs money
```

The offline tests check the demo pipeline against values worked out by hand from
`scripts/build_demo_fixture.py`. They also cover restatements, a 10-K/A, a quarterly value
labelled as fiscal-year, duplicates and competing tags, sign handling, missing data,
balance-check failures, zero and negative denominators, retry and pacing behaviour, cache
reuse, bounded recovery, and resuming after a simulated crash. Stage 2 tests cover 10-K
section parsing (table of contents, running headers, notes), quote verification, the typed
tools (allowlist, invalid input, source budget), the step budget, fabricated quotes,
status rules, run budgets, LLM outages with resume, review revision cycles and their cap,
and every reviewer check against tampered drafts. Claude-only paths are tested with a
scripted fake model and a fake SDK client.

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
  export.py        CSV / JSON / report exports and display tables
  evidence/        10-K HTML -> section-aware passages; BM25 search; quote verification
  investigator/    typed tools, bounded loop, investigation stage
  llm/             provider interface, Anthropic provider, offline stub, provider choice
  report/          report writer (Markdown + HTML), chart specs and rendering
  review/          deterministic + LLM reviewer
  presentation.py  number formatting and the parser the reviewer checks it with
  cli.py
config/            settings.toml, mappings.toml, companies/
fixtures/demo/     SYNTHETIC offline fixture (labelled), including synthetic 10-K text
scripts/           fixture generator
tests/unit/        offline tests
tests/integration/ live SEC tests (opt-in)
docs/              architecture and data policies
```

## Roadmap

1. **Stage 1:** data pipeline, CLI, offline demo.
2. **Stage 2 (this release):** filing-text retrieval, investigator with typed tools and step
   budgets, evidence-backed findings, report (Markdown/HTML) with charts and citations,
   reviewer with at most two revision cycles, Anthropic SDK behind a provider interface.
   Still to do: the single-prompt baseline for comparison.
3. **Stage 3:** Streamlit dashboard, downloads, scheduled check for new 10-K filings.
4. **Stage 4:** uploaded reports, industry profiles, peers.
