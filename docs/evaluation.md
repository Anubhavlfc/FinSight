# Evaluating the agentic investigation

FinSight investigates each flagged change in a bounded tool loop: the model searches the 10-K,
reads passages, looks up validated metrics and records a finding. `finsight evaluate` measures what
that loop adds over the simplest alternative: **one prompt** with the same starting information.

```bash
finsight analyze MSFT                 # with ANTHROPIC_API_KEY set, so the investigator is Claude
finsight evaluate <run_id>            # one more Claude call; writes out/<run_id>/evaluation.{json,md}
finsight evaluate <run_id> --excerpts 10
```

## The baseline

The baseline is given (`finsight/evaluation/baseline.py`):

* the calculated observation for each flagged change, written by code from validated metrics,
  exactly as the investigator receives it;
* for each change, the top *N* passages (default 6) from the same BM25 index over the 10-K text the
  run loaded, using the same suggested query the investigator gets, from the 10-K of the later year
  first;

and must answer every change in **one call** through one tool, `record_findings`. It cannot search
again, read other passages, look up other metrics or load another filing.

## Same scoring for both

Baseline answers go through the investigator's own code (`build_finding`): every quote is checked
verbatim against the passage it cites, an explanation with no verified quote is dropped, and the
evidence status is set by code (the model's proposal can only lower it). The comparison then counts,
for each approach:

| Measure | Meaning |
|---|---|
| Explained with a verified quote | Changes with a management explanation backed by at least one verbatim quote |
| Supported / partially / unsupported | Code-assigned evidence status |
| Quotes proposed, verified, failure rate | How often the model's quotes were not in the cited passage (fabricated or misattributed) |
| Explanation numbers not in quotes or metrics | Figures in the explanation that neither a verified quote nor a cited metric contains (the reviewer's `explanation_numbers` rule) |
| Model calls, tokens, estimated cost | Agentic figures cover the stored run's whole investigation stage |

The agentic column is read from the stored run, so the comparison costs one model call.

## Results

No live comparison has been recorded yet. On 2026-11-14 the Anthropic account used by the
development environment had no API credit, so neither a Claude investigation nor the baseline
call could run. The harness is covered offline by `tests/unit/test_evaluation.py` with a scripted
model: verified, fabricated and missing answers, a reply without the tool call, and unsupported
numbers in an explanation. Once credit is available, run the two commands above for a few
companies and add the tables here.
