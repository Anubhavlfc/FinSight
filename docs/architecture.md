# FinSight architecture (Stage 1)

FinSight answers: *How has this company performed over its last three completed fiscal
years, what changed most, and what evidence helps explain those changes?*

Stage 1 delivers the foundation: a reliable, traceable path from a company identifier
to a validated three-year financial dataset with calculated metrics and ranked changes.
Investigation, report writing and review (Stage 2) and the dashboard (Stage 3) build on
these contracts.

## Workflow

```
CLI / (Stage 3) Streamlit
        │
   Orchestrator ── WorkflowRun state + per-stage checkpoints (SQLite)
        │
 PLANNING ─ resolve company ─┬─ ambiguous name ─────────────▶ NEEDS_INPUT ─(user picks CIK)─▶ PLANNING
        │                    └─ bank / insurer / 20-F filer ─▶ FAILED (explained)
        ▼
 COLLECTING ─ SEC client: User-Agent, ≤5 req/s, 3 attempts w/ backoff+jitter, disk cache
        │      └─ retries exhausted ─▶ FAILED (resume re-enters COLLECTING)
        ▼
 VALIDATING ─ annual-period filter → restatement policy → tag selection → checks
        │      ├─ company facts predate latest 10-K ─▶ COLLECTING (refetch, max 1) ─▶ else blocking
        │      ├─ warning: continue, disclosed
        │      └─ blocking: affected metrics unavailable
        ▼
 ANALYZING ─ deterministic metrics + rule-based change flags
        ▼
 [Stage 2: INVESTIGATING → DRAFTING → REVIEWING (≤2 revision cycles)]
        ▼
 READY_FOR_REVIEW (no blocking issues)  or  PARTIAL (blocking issues disclosed)
        ▼
 APPROVED (explicit analyst action; never automatic)
```

### Allowed transitions

| From | To | Cause |
|---|---|---|
| CREATED | PLANNING | run started |
| PLANNING | COLLECTING / NEEDS_INPUT / FAILED | plan built / ambiguous identity / unsupported, not found, no 10-K |
| NEEDS_INPUT | PLANNING / FAILED | user clarifies company |
| COLLECTING | VALIDATING / FAILED | facts collected / retrieval failed after bounded retries |
| VALIDATING | ANALYZING / COLLECTING / FAILED | validated / bounded refetch recovery |
| ANALYZING | READY_FOR_REVIEW / PARTIAL / INVESTIGATING (Stage 2) / FAILED | |
| INVESTIGATING | DRAFTING / PARTIAL / FAILED | Stage 2 |
| DRAFTING | REVIEWING / FAILED | Stage 2 |
| REVIEWING | DRAFTING / INVESTIGATING / READY_FOR_REVIEW / PARTIAL / FAILED | Stage 2 revision loop, capped |
| READY_FOR_REVIEW | APPROVED / DRAFTING / COLLECTING | analyst approves / requests revision / refresh |
| PARTIAL | APPROVED / COLLECTING / PLANNING | |
| FAILED | the stage that failed | `finsight resume` |

Defined in `finsight/workflow.py`; any other transition raises `InvalidTransitionError`.
The orchestrator also has a hard step limit so no loop can run away.

## Responsibilities

| Role | Implementation | Input → output | Tools / data | Failure handling |
|---|---|---|---|---|
| Intake & planner | `resolver.py`, `planner.py` (code) | `AnalysisRequest` → `AnalysisPlan` | company tickers, submissions | ambiguous → NEEDS_INPUT; unsupported → FAILED with reason |
| Data collector | `sec/`, `collector.py` (code) | plan → `CollectionResult` + `SourceDocument`s | SEC client, cache, fixtures | bounded retry; cache; resumable |
| Normalizer | `normalizer.py` (code) | raw XBRL rows → accepted / alternative / rejected facts | `config/mappings.toml` | alternate tags, derivation, disclosed issues |
| Validator | `validator.py` (code) | facts → `ValidationReport` | rounding-aware checks | issues, never balancing entries |
| Analysis engine | `metrics.py` (code) | report → `CalculatedMetric`s | — | unavailable / suppressed with reason |
| Change detector | `changes.py` (code) | metrics → ranked `ChangeFlag`s | `[analysis]` settings | — |
| Investigator | Stage 2: LLM + typed tools | flags → `InvestigationFinding`s | `search_filing_sections`, `read_source_passage`, `get_validated_metric`, `compare_financial_components`, `request_additional_source` | step budget, then disclose the gap |
| Writer & reviewer | Stage 2: LLM + deterministic checks | findings → report, `ReviewResult` | — | ≤2 revisions, then needs analyst review |

## Data policies

**Fiscal periods.** Period ends come from the `reportDate` of original 10-K filings filed
on or before the cutoff. A duration fact is annual only if it spans 350–380 days and ends
within 3 days of that date; an instant fact must have no start date. XBRL `fy`/`fp` labels
are ignored, because a 10-K labels its own Q4 figure `fy=<year>, fp=FY`.

**Restatements.** For each field, tag and period, the value from the most recently filed
annual report (10-K or 10-K/A, filed by the cutoff) is accepted. Earlier values are kept as
`ALTERNATIVE` facts, and a `restated_value` warning records old and new values and both
accession numbers. Changing the cutoff therefore reproduces what was knowable at that date.

**Tags.** Each standardized field has an ordered tag list (`config/mappings.toml`, with
per-company overrides in `config/companies/<CIK>.toml`). The first tag that covers every
year in the window is used so the years stay comparable; otherwise the best tag per year
is used with a `mixed_tags_across_years` warning. Competing tags are kept as rejected
candidates, with a warning if their values differ.

**Consolidated vs segment.** The companyfacts API only exposes facts without XBRL
dimensions, i.e. consolidated totals. Segment data needs the filing itself (Stage 2).

**Currency and scale.** Only `USD` units are accepted. XBRL values are already in units
(no thousands/millions scaling); presentation tables divide by 10⁶ for display only.

**Signs.** Capital expenditure is expressed as a positive outflow. A negative reported
value is flipped and disclosed with a `sign_normalized` warning.

**Missing data.** Alternative tags are tried first, then configured derivations (gross
profit = revenue − cost of revenue, marked `DERIVED` with input fact IDs). A required field
that is still missing is a blocking issue for the metrics that need it. Nothing is
estimated. Extraction from the filing text is the Stage 2 fallback.

**Accounting checks** (tolerance = max(USD 5m, 0.1%)): assets = liabilities and equity;
assets = liabilities + equity; gross profit = revenue − cost of revenue; long-term debt =
noncurrent + current; current assets ≤ total assets; current liabilities ≤ total
liabilities; cash ≤ current assets. A failure is a warning with the numbers attached.

**Issue severity.** `info` (disclosed decision), `warning` (continue with limitation),
`recoverable` (the workflow retries, e.g. refetching stale company facts), `blocking`
(affected metrics unavailable; run ends PARTIAL).

## Metrics

See the docstring in `finsight/metrics.py` for formulas. Key choices:

* **Free cash flow** = operating cash flow − capital expenditure (purchases of PP&E). It is a
  calculated, non-GAAP measure and labelled as such.
* **Interest-bearing debt** = long-term debt including current maturities (`LongTermDebt`,
  else `LongTermDebtNoncurrent` + `LongTermDebtCurrent`) + commercial paper + short-term
  borrowings. Leases are excluded. Total liabilities is never substituted. Components
  that are not reported are named in the metric's note.
* **Growth** is only computed from a positive base. From a zero or negative base the
  percentage is `suppressed` and the absolute change is given.
* **Margins** need positive revenue. Margin changes are in percentage points.
* Values are `Decimal` end to end. Rounding happens only in presentation tables.

## Persistence and caching

* HTTP cache (`.finsight/cache/http/`): raw bytes + retrieval time + SHA-256. Filing
  history is re-checked after `submissions_ttl_seconds`; company facts are reused only if
  they already contain the latest 10-K accession from filing history.
* SQLite (`.finsight/finsight.sqlite3`): `runs`, `transitions`, `checkpoints` (one JSON per
  completed stage), and query tables `sources`, `facts`, `issues`, `metrics`, `changes`.
* A run interrupted mid-stage resumes at that stage with all earlier checkpoints reused.

## What Stage 2 adds

Filing-text retrieval with section-aware chunks (MD&A, notes, segments), the investigator
loop with the typed tools above and a per-finding step budget, `InvestigationFinding`s that
separate calculated observation / management explanation / analyst interpretation /
unresolved question, report drafting with claim-level citations, and a reviewer that
re-checks numbers deterministically before an LLM checks narrative support. Retrieved text
is treated as untrusted data. A single-prompt baseline will be built alongside it so the
value of the agentic loop can be measured.
