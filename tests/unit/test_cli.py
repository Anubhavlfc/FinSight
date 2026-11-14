"""CLI smoke tests against the SYNTHETIC fixture (no network)."""

from __future__ import annotations

import csv
import json

from finsight.cli import FIXTURE_BANNER, main


def test_offline_analyze_writes_exports(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FINSIGHT_SEC_USER_AGENT", raising=False)
    db, out = tmp_path / "db.sqlite3", tmp_path / "out"
    code = main(["--db", str(db), "--out", str(out), "analyze", "DEMO", "--offline", "--cutoff", "2026-11-01"])
    text = capsys.readouterr().out
    assert code == 0
    assert FIXTURE_BANNER in text and "READY FOR REVIEW (not approved)" in text
    run_dir = next(out.iterdir())
    names = {p.name for p in run_dir.iterdir()}
    assert {"financial_dataset.csv", "metrics.csv", "facts_audit_trail.csv", "validation_issues.csv",
            "analysis.json", "run_summary.json"} <= names
    rows = {r["field"]: r for r in csv.DictReader((run_dir / "financial_dataset.csv").open())}
    assert rows["revenue"]["FY2026"] == "1239000000"
    payload = json.loads((run_dir / "analysis.json").read_text())
    assert payload["run"]["state"] == "READY_FOR_REVIEW"
    assert all(s["is_fixture"] for s in payload["sources"])

    run_id = run_dir.name
    # Approval needs the reviewer to say what they checked.
    assert main(["--db", str(db), "approve", run_id, "--reviewer", "Dana"]) == 2
    assert "To approve" in capsys.readouterr().err
    assert main(["--db", str(db), "review", run_id]) == 0
    listing = capsys.readouterr().out
    assert "Investigated changes" in listing and "change:" in listing
    assert main(["--db", str(db), "approve", run_id, "--reviewer", "Dana", "--confirm-all",
                 "--figures-checked", "--limitations-read", "--note", "checked"]) == 0
    assert main(["--db", str(db), "show", run_id]) == 0
    assert "state=APPROVED" in capsys.readouterr().out


def test_live_mode_without_user_agent_explains(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FINSIGHT_SEC_USER_AGENT", raising=False)
    code = main(["--db", str(tmp_path / "db.sqlite3"), "--out", str(tmp_path / "out"), "analyze", "MSFT"])
    out = capsys.readouterr().out
    assert code == 2 and "FINSIGHT_SEC_USER_AGENT" in out


def test_needs_input_exit_code(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "db.sqlite3"), "--out", str(tmp_path / "out"),
                 "analyze", "Demo Synthetic", "--offline", "--cutoff", "2026-11-01"])
    out = capsys.readouterr().out
    assert code == 3 and "Which company did you mean" in out and "finsight resume" in out
