# Golden fixture: REAL SEC data, for accuracy tests only

Unlike `fixtures/demo/`, everything here is **real** data recorded from SEC EDGAR for
Microsoft, Apple, Walmart and Coca-Cola by `scripts/record_golden_fixture.py` (see
`recorded.json` for when, and the SHA-256 of each untrimmed response). The files are
trimmed to the filing-index columns and XBRL concepts FinSight reads.

`tests/unit/test_golden_filings.py` replays them offline with a 2026-11-01 cutoff and compares
the results with figures typed by hand from each company's 10-K statements
(`tests/golden/expected.toml`).

This folder is not meant for `finsight --fixtures`: the CLI and report label every offline
fixture as synthetic, which would be wrong for this data. Run real companies live instead.

To refresh after new 10-Ks are filed: re-run the script, move the cutoff in the test, and
re-type the expected figures from the new statements. Never copy them from FinSight's output.
