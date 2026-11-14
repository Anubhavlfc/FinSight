# FinSight architecture

FinSight answers: *How has this company performed over its last three completed fiscal
years, what changed most, and what evidence helps explain those changes?*

Stage 1 delivers the foundation: a reliable, traceable path from a company identifier
to a validated three-year financial dataset with calculated metrics and ranked changes.
Stage 2 investigates the ranked changes in the company's 10-K text, writes a cited report
and reviews it. Stage 3 puts a web API and a React app on top of the same contracts.

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
 INVESTIGATING ─ load 10-K text (MD&A, notes) → per flag: bounded tool loop → finding
        │      ├─ quotes checked verbatim by code; status can only be lowered
        │      ├─ LLM budget used up ─▶ stop, remaining flags disclosed as not investigated
        │      └─ LLM outage after SDK retries ─▶ FAILED (resume keeps finished findings)
        ▼
 DRAFTING ─ report from metrics + findings only (every claim cites metric / passage IDs)
        ▼
 REVIEWING ─ deterministic checks (+ LLM narrative review when Claude is used)
        │      ├─ defect in a finding ─▶ INVESTIGATING (once, those flags only)
        │      ├─ defect in a claim ─▶ DRAFTING (claim removed or status lowered, disclosed)
        │      └─ after 2 revision cycles ─▶ keep defects, flag "needs analyst attention"
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
| ANALYZING | INVESTIGATING / READY_FOR_REVIEW / PARTIAL / FAILED | investigate (default) / `--no-investigate` |
| INVESTIGATING | DRAFTING / PARTIAL / FAILED | findings recorded / LLM outage or misconfiguration |
| DRAFTING | REVIEWING / FAILED | report drafted |
| REVIEWING | DRAFTING / INVESTIGATING / READY_FOR_REVIEW / PARTIAL / FAILED | revision loop (max `max_revision_cycles`, default 2) / done |
| READY_FOR_REVIEW | APPROVED / DRAFTING / COLLECTING | analyst approves / requests revision / refresh |
| PARTIAL | APPROVED / COLLECTING / PLANNING | |
| FAILED | the stage that failed (incl. INVESTIGATING, DRAFTING, REVIEWING) | `finsight resume` |

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
| Filing text | `evidence/text.py`, `evidence/search.py` (code) | 10-K HTML → `FilingPassage`s; BM25 search | SEC archive, cache | missing text → disclosed limitation |
| Investigator | `investigator/` (LLM + typed tools, loop in code) | `ChangeFlag` → `InvestigationFinding` | `search_filing_sections`, `read_source_passage`, `get_validated_metric`, `compare_financial_components`, `request_additional_source`, `record_finding` | 3 tool-using steps + 1 wrap-up per finding; run-level call/token budget |
| Report writer | `report/` (code) | metrics + findings → `ReportDraft` (claims, charts, Markdown, HTML) | matplotlib | claims removed by review are disclosed |
| Reviewer | `review/reviewer.py` (code + LLM) | draft → `ReviewResult` | deterministic checks; `submit_review` | routes defects back; ≤2 cycles, then needs analyst attention |

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

**Derived gross profit and total revenue.** When gross profit is derived as revenue − cost of
revenue and total revenue (`Revenues`) is larger than contract revenue
(`RevenueFromContractWithCustomer…`), a `derived_on_total_revenue` warning gives the
difference. Walmart is the case: its gross profit is net sales − cost of sales, so FinSight's
derived figure is higher by membership and other income, and the margins are not comparable.

**Missing data.** Alternative tags are tried first, then configured derivations (gross
profit = revenue − cost of revenue; total liabilities = liabilities and equity − total
equity; both marked `DERIVED` with input fact IDs). A required field
that is still missing is a blocking issue for the metrics that need it. Nothing is
estimated. Extraction from the filing text is the Stage 2 fallback.

**Accounting checks** (tolerance = max(USD 5m, 0.1%)): assets = liabilities and equity;
assets = liabilities + equity; gross profit = revenue − cost of revenue; long-term debt =
noncurrent + current; current assets ≤ total assets; current liabilities ≤ total
liabilities; cash ≤ current assets. A failure is a warning with the numbers attached. The
assets = liabilities + equity check is skipped when total liabilities was derived (it would
be circular). The long-term debt check is `info`, because filers often tag `LongTermDebt` at
face value and the balance-sheet lines at carrying amount.

**Competing tags.** When a lower-priority tag reports a different value, that is a warning
by default. Fields whose fallback tags differ by definition (net income including
noncontrolling interests, cash including restricted cash, equity excluding noncontrolling
interests) set `competing_difference_severity = "info"` in `config/mappings.toml`.
Revenue prefers `us-gaap:Revenues` (total revenue) over the ASC 606 tag, which leaves out
membership, lease and financing revenue at filers such as Walmart and GM.

**Issue severity.** `info` (disclosed decision), `warning` (continue with limitation),
`recoverable` (the workflow retries, e.g. refetching stale company facts), `blocking`
(affected metrics unavailable; run ends PARTIAL).

## Metrics

See the docstring in `finsight/metrics.py` for formulas. Key choices:

* **Free cash flow** = operating cash flow − capital expenditure (purchases of PP&E). It is a
  calculated, non-GAAP measure and labelled as such.
* **Interest-bearing debt** = long-term debt including current maturities
  (`LongTermDebtNoncurrent` + `LongTermDebtCurrent`, the balance-sheet carrying amounts,
  else `LongTermDebt`) + commercial paper + short-term borrowings (`ShortTermBorrowings`, else
  `OtherShortTermBorrowings`, which the taxonomy defines as a component of it). Leases are excluded,
  except at filers that tag debt and finance leases together
  (`LongTermDebtAndCapitalLeaseObligations*`), where the metric note says so. Total
  liabilities is never substituted. Components that are not reported are named in the
  metric's note.
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

## Stage 2: investigation, report and review

### Filing text

The 10-K primary document for each fiscal year that a flagged comparison ends in is
downloaded through the same SEC client and cache (filed documents never change, so a
cached copy is always reused). `evidence/text.py` turns HTML into one line per block
element, finds `Item N.` headings (a 10-K repeats them in its table of contents and as
running page headers, so a new section starts only when the item changes and the longest
occurrence wins), splits Item 8 into numbered notes, and chunks each section into
paragraph-aligned passages of about 1,200 characters. Each `FilingPassage` keeps the
accession, form, filing date, fiscal period, section, source URL, source document hash and
its character range in the extracted text. Search is BM25 over those passages. Embeddings
are not used: add them only if evaluation shows keyword search misses evidence.

### Investigator

For each flag, in rank order up to `max_findings`, the orchestrator runs a bounded loop:
the model gets the calculated observation (written by code) and a small allowlisted tool
set; at most `max_steps_per_finding` (3) turns may call investigative tools, then one
wrap-up turn may only record. Tool inputs are validated with Pydantic; unknown tools and
invalid input come back as error results. `request_additional_source` can only load 10-Ks
in the analysis window, within a per-run budget. Filing text is returned inside an
`untrusted_filing_text` field and the system prompt says it is data that cannot change the
task (the demo fixture contains an injection line to exercise this).

`record_finding` separates **management explanation** (attributed, backed by verbatim
quotes), **analyst interpretation** (optional, labelled, no unsupported causal claims) and
**unresolved questions**. Code then decides the evidence status:

| Status | Rule |
|---|---|
| supported | explanation present, every quote verified, no open questions |
| partially_supported | explanation with at least one verified quote, but some quotes failed or questions remain |
| unsupported | no verified quote (the explanation is then dropped and disclosed as unverified) |

The model's proposed status can lower this, never raise it. A quote that fails
verification is sent back once (within the step budget) with the reason.

### Report writer

The writer adds no facts. Numbers enter prose only through `presentation.fmt_metric`
from a `CalculatedMetric`; management statements only from verified findings. Every
statement is a `ReportClaim` with metric IDs and/or passage IDs, rendered with `[Mn]` /
`[Pn]` markers that resolve to the calculation notes and source list. Sections: executive
summary, three-year table, four charts (each with periods, units, a CSV and the plotted
values as a table), ranked changes, explanations, risks and missing information, sources,
calculation notes and method. Markdown and HTML come from one block list; the exported
HTML embeds the charts so it is a single file.

### Reviewer

Deterministic checks decide pass/fail: numbers in each claim match a metric the claim
cites (at the displayed precision and unit), cited metrics exist and are available, every
`[Pn]`/`[Mn]` resolves, quotes still match their passages, numbers in a management
explanation appear in its quotes or cited metrics, claim and finding statuses agree,
interpretations make no unhedged causal claim, every unavailable table metric is
disclosed, chart values equal their metrics, and fixture/stub/not-approved labels are
present. With Claude, an LLM then judges whether each quote set supports its explanation;
its verdict can only lower a status. Defects route to investigation (once, for the
affected flags) or drafting (claim removed or status lowered, and disclosed). After
`max_revision_cycles` the remaining defects stay in `review.json` and the run is marked as
needing analyst attention.

### LLM provider

`llm/provider.py` defines `LLMProvider` / `LLMSession`. `AnthropicProvider` uses the
official SDK: model from `FINSIGHT_LLM_MODEL` (default `claude-opus-5-5`), effort from
settings, tools passed as JSON Schema from the Pydantic inputs, assistant content passed
back unchanged, server-side refusal fallback on, SDK retries for 429/5xx/connection
errors. Errors map to `LLMTransientError` (resumable), `LLMConfigurationError`,
`LLMRefusal` (finding recorded as unsupported) and `BudgetExceeded` (stop gracefully).
Usage (calls, tokens, estimated cost from configured prices) is tracked per run.

`llm/stub.py` is the offline stand-in. It is rule-based, not an AI model: it quotes a
sentence only when the flagged item is its subject, it contains explicit attribution
language and it moves in the flagged direction; otherwise it reports the change as
unexplained. It writes no interpretation and the LLM review is skipped. It is labelled in
the report, the findings and the run stats. On real 10-Ks it is deliberately
conservative: in testing it explained 1 of 5 MSFT changes, 0 of 4 for AAPL and KO, where
the filings do discuss them in less formulaic sentences. That gap is what the Claude
investigator is for.

## Stage 3: web app

```
browser (React + TypeScript, web/)
   │  JSON over HTTP, types generated from docs/openapi.json
   ▼
FastAPI (finsight/api/)  ── serves web/dist as static files in production
   │  schemas.py   request and response models: the contract
   │  app.py       routes, validation, errors, limits, security headers
   │  directory.py company search and profile (reuses resolver + planner rules)
   │  jobs.py      background runs on a small thread pool
   │  views.py     engine results -> response models (no new calculations)
   ▼
Orchestrator and stages (unchanged)  ──  SQLite (runs, transitions, checkpoints), out/<run_id>/
```

**Thin layer.** The API adds no financial logic. Every figure, comparison, evidence status
and sentence in a response is read from the engine's checkpoints or formatted by
`presentation.py`, the same code the report writer and reviewer use. The frontend formats
dates and file sizes and nothing else.

**Contract.** `schemas.py` is the single definition. `scripts/export_openapi.py` writes
`docs/openapi.json`, and `npm run gen:api` turns that into `web/src/api/schema.d.ts`. CI
fails if either file is stale, so a backend change that the frontend has not caught up with
breaks the build rather than the page.

**Runs are background jobs.** `POST /api/runs` validates the request against the company's
profile (scope, available fiscal years), stores the run and returns its ID with `202`. A
worker thread drives the orchestrator with its own SQLite connection. The page polls
`GET /api/runs/{id}` once a second. Progress is the state machine's own history: a stage is
"done" when its checkpoint and transition exist, so there is nothing to simulate. Because
the run ID is in the URL and the state is in SQLite, a refresh, a second tab or a shared
link all reconnect to the same run.

| Situation | Behaviour |
|---|---|
| Same request submitted twice while running | The second call returns the first run's ID (`reused: true`) |
| Queue full, or more than `FINSIGHT_RUNS_PER_HOUR` from one address | `429` with `Retry-After` and next steps |
| A stage fails (for example SEC unreachable) | Run is `failed` with the stage, a plain reason and next steps; `POST …/resume` continues from that stage |
| Server restarted mid-run | Run is reported as `interrupted`; resume continues from the last checkpoint |
| Run exceeds `FINSIGHT_RUN_BUDGET_SECONDS` | Stopped between stages as `failed`, resumable |
| Blocking data issue | Run finishes as `partial`: affected metrics are unavailable with a reason, nothing is estimated |
| Live SEC access not configured | Health says so; real tickers get `503 live_unavailable`; only the synthetic company runs |

**Review state.** `ready_for_review` and `partial` reports are "awaiting analyst review" until a
person records a decision with `POST …/review` (optionally protected by
`FINSIGHT_REVIEWER_TOKEN`). The body carries the reviewer's name, a verdict per investigated change
(confirmed / disputed / not checked), the two checks and a note. `Orchestrator.record_review`
applies the rules in `finsight/review/human.py`, which the CLI and the dashboard use too: approval
needs every change confirmed and both checks; a change request needs a note or a disputed change
and leaves the run unapproved (review state `changes_requested`). Every decision is kept in
`run.stats.human_reviews` and returned as `review.history`.

**Safety.** Input is validated by Pydantic models that forbid unknown fields; company
identifiers and run IDs are pattern-checked. Downloads are restricted to files inside the
run's own output folder with a known extension. Filing text is rendered as text by React
(never as HTML), and generated HTML reports are served with a `sandbox` Content Security
Policy. CORS is off unless `FINSIGHT_CORS_ORIGINS` names origins. API keys exist only in the
server's environment. Errors return a code, a message and next steps, never a stack trace.

**Samples.** `scripts/build_web_sample.py` runs the pipeline through the API and saves the
responses and files under `web/public/sample/`. The landing page preview and the sample
report render them with the same components as a live run, so they work on a static host
and when the backend is down. A test validates them against the current contract.

**Known limits of this design.** One process: jobs live in the web process, so run a single
instance with a single worker. Rate limits are in memory and per process. There are no
user accounts, so every visitor sees the same archive and can read any report by its ID;
do not put confidential questions into a public deployment. Polling, not streaming.
No PDF export: reports download as HTML and Markdown, data as CSV and JSON, charts as PNG.

### Not yet built

* The single-prompt baseline (validated tables + retrieved excerpts → one report prompt)
  for measuring what the agentic loop adds. Planned next, once runs with Claude are
  available to compare.
* Re-running investigation or drafting automatically after a change request. Today the request is
  recorded for whoever revises the report; the analyst then starts a new run.
