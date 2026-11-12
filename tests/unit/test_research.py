"""Research report (PDF): valuation arithmetic, rating rule, narrative verification and the rendered PDF.

Runs offline on the SYNTHETIC demo fixture; no network and no API key.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pypdf import PdfReader

from finsight.contracts import AnalysisRequest, CalculatedMetric, FilingPassage, RunState
from finsight.orchestrator import Orchestrator, load_results
from finsight.research.builder import ResearchError, build_research
from finsight.research.models import MarketSnapshot
from finsight.research.narrative import TOPICS, draft_topic, extract
from finsight.research.service import generate, manual_snapshot, render
from finsight.research.valuation import value
from finsight.sec.sources import FixtureSource
from tests.conftest import DEMO_FIXTURES
from tests.llm_fakes import ScriptedProvider, tool_turn

D = Decimal
SECTIONS = ["Investment Summary", "Business Description", "Industry Overview and Competitive Positioning",
            "Valuation", "Financial Analysis", "Investment Risks", "Environmental, Social and Governance"]


@pytest.fixture
def demo(settings, store):
    orch = Orchestrator(settings, store, lambda req: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True))
    return run, load_results(store, run.id)


def _snap(price="40", ticker="DEMO") -> MarketSnapshot:
    return manual_snapshot(ticker, price, date(2026, 11, 2), "Test price (synthetic)")


# --------------------------------------------------------------------------- market valuation


def _metric(name, period, val, unit="USD"):
    return CalculatedMetric(id=f"metric:{name}:{period}", name=name, fiscal_period=period, value=D(val), unit=unit,
                            formula="test")


def _metrics(fcf=20):
    return [_metric("revenue", "FY2", 200), _metric("net_income", "FY2", 25), _metric("operating_income", "FY2", 40),
            _metric("free_cash_flow", "FY2", fcf), _metric("cash_and_equivalents", "FY2", 30),
            _metric("interest_bearing_debt", "FY2", 50)]


def test_market_value_and_multiples_match_a_hand_calculation():
    v = value(_metrics(), ["FY1", "FY2"], D(10), D(5), _snap("50"))
    assert v.available and v.market_cap == D(500)
    assert v.net_cash == D(30) + D(5) - D(50) and v.enterprise_value == D(515)
    got = {m.name: m.value for m in v.multiples}
    assert got == {"P/E": "20.0x", "P/FCF": "25.0x", "FCF yield": "4.0%", "EV / revenue": "2.6x",
                   "EV / operating income": "12.9x"}


def test_negative_denominators_are_not_meaningful():
    v = value(_metrics(fcf=-5), ["FY1", "FY2"], D(10), None, _snap("50"))
    assert {m.name: m.value for m in v.multiples}["P/FCF"] == "n/m"


def test_no_price_or_missing_inputs_are_named_instead_of_guessed():
    assert not value(_metrics(), ["FY1", "FY2"], D(10), None, None).available
    v = value([_metric("revenue", "FY2", 200)], ["FY1", "FY2"], None, None, _snap())
    assert not v.available
    for word in ("cash and cash equivalents", "interest-bearing debt", "diluted share count"):
        assert word in v.reason


# --------------------------------------------------------------------------- narrative verification


def _passage(pid="psg:1:item_1:000", text=None):
    return FilingPassage(id=pid, accession="1", form="10-K", filing_date=date(2026, 1, 1), fiscal_period="FY2",
                         section="item_1", section_title="Item 1. Business",
                         text=text or ("We design and sell analytics software to enterprise customers worldwide. "
                                       "We had 1,200 employees at year end."),
                         char_start=0, char_end=100, source_document_id="doc", url="https://example.test/f")


def test_ai_paragraphs_survive_only_with_verbatim_quotes_and_no_stray_numbers():
    p = _passage()
    good = {"text": "The company sells analytics software to enterprises.",
            "quotes": [{"passage_id": p.id, "quote": "We design and sell analytics software to enterprise customers"}]}
    invented_quote = {"text": "The company is the market leader.",
                      "quotes": [{"passage_id": p.id, "quote": "We are the undisputed leader in every market"}]}
    stray_number = {"text": "The company employs 5,000 people.",
                    "quotes": [{"passage_id": p.id, "quote": "We had 1,200 employees at year end"}]}
    provider = ScriptedProvider(lambda *a: tool_turn(("submit_section", {
        "paragraphs": [good, invented_quote, stray_number]})))
    paras, notes = draft_topic([p], TOPICS["business"], "Demo", provider)
    assert [x.text for x in paras] == [good["text"]] and paras[0].kind == "ai_summary"
    assert len(notes) == 2 and "none of its quotes matched" in notes[0] and "5,000" in notes[1]


def test_extracts_are_verbatim_and_used_without_a_model():
    p = _passage()
    paras, notes = draft_topic([p], TOPICS["business"], "Demo", None)
    assert paras and paras[0].kind == "filing_extract" and not notes
    assert paras[0].text.strip("“”") in p.text


def test_model_that_never_submits_falls_back_to_extracts():
    provider = ScriptedProvider(lambda *a: tool_turn())
    paras, notes = draft_topic([_passage()], TOPICS["business"], "Demo", provider)
    assert paras[0].kind == "filing_extract" and "AI drafting failed" in notes[0]


# --------------------------------------------------------------------------- end to end on the demo run


def test_report_has_every_required_field_and_section_and_fits_ten_pages(demo, settings, store, tmp_path):
    run, results = demo
    rep = generate(store, settings, run, results, _snap(), FixtureSource(DEMO_FIXTURES), use_llm=False)
    assert [s.title for s in rep.sections] == SECTIONS
    assert rep.valuation.available and rep.valuation.market_cap == D(40) * rep.valuation.diluted_shares
    # every citation marker used in the text resolves to a listed source
    ids = {c.id for c in rep.citations}
    for s in rep.sections:
        for p in s.paragraphs + s.bullets:
            assert set(p.citation_ids) <= ids
    # traceable: the base FCF and the CAGR inputs are engine metric IDs that exist
    metric_ids = {m.id for m in results["analysis"].metrics}
    for a in rep.assumptions:
        assert set(a.metric_ids) <= metric_ids
    assert load_results(store, run.id)["research"].run_id == run.id

    out = tmp_path / "r.pdf"
    results["research"] = rep
    stats = render(rep, results, out)
    assert stats.body_pages <= 10 and stats.appendix_pages <= 10
    text = "\n".join(page.extract_text() for page in PdfReader(str(out)).pages)
    flat = " ".join(text.split())
    for label in ("Company name", "Exchange", "Ticker symbol", "Sector", "Industry", "Current price",
                  "as of 02 November 2026", "Trading multiples"):
        assert label in flat, label
    for title in SECTIONS:
        assert title in flat, title
    assert "SYNTHETIC DEMO DATA" in flat
    assert "DRAFT · NOT APPROVED" in flat and "Not investment advice" in flat
    for word in ("CFA Institute", "front cover", "Recommendation", "Target price", "BUY", "SELL", "HOLD", "DCF"):
        assert word not in flat, word
    assert "DEMO" in PdfReader(str(out)).pages[0].extract_text()  # page 1 is the report, not a cover


def test_without_a_price_the_report_still_renders(demo, settings, store, tmp_path):
    run, results = demo
    rep = generate(store, settings, run, results, None, FixtureSource(DEMO_FIXTURES), use_llm=False)
    assert not rep.valuation.available and "No current share price" in rep.valuation.reason
    results["research"] = rep
    assert render(rep, results, tmp_path / "x.pdf").body_pages <= 10


def test_price_for_another_ticker_is_refused(demo, settings, store):
    run, results = demo
    with pytest.raises(ResearchError):
        generate(store, settings, run, results, _snap(ticker="MSFT"), FixtureSource(DEMO_FIXTURES), use_llm=False)


def test_unfinished_run_is_refused(demo, settings):
    run, results = demo
    with pytest.raises(ResearchError):
        build_research(run.model_copy(update={"state": RunState.COLLECTING}), results, None, None, None)


def test_approval_removes_the_draft_watermark(demo, settings, store, tmp_path):
    run, results = demo
    orch = Orchestrator(settings, store, lambda req: FixtureSource(DEMO_FIXTURES))
    run = orch.record_review(run.id, "approved", "Test Reviewer", "ok", {})
    results = load_results(store, run.id)
    rep = generate(store, settings, run, results, _snap(), FixtureSource(DEMO_FIXTURES), use_llm=False)
    assert rep.review_status.startswith("Underlying analysis approved by Test Reviewer")
    results["research"] = rep
    render(rep, results, tmp_path / "a.pdf")
    text = " ".join(" ".join(p.extract_text() for p in PdfReader(str(tmp_path / "a.pdf")).pages).split())
    assert "NOT APPROVED" not in text


def test_cli_writes_the_pdf(demo, settings, store, tmp_path, monkeypatch, capsys):
    from finsight.cli import main

    run, _ = demo
    monkeypatch.setenv("FINSIGHT_HOME", str(settings.home))
    out = tmp_path / "cli.pdf"
    code = main(["--db", str(store.path), "research-pdf", run.id, "--price", "40", "--price-date", "2026-11-02",
                 "--price-source", "Test price", "--output", str(out)])
    assert code == 0 and out.stat().st_size > 10_000
    assert "PDF:" in capsys.readouterr().out


def test_extract_skips_short_fragments():
    assert extract([_passage(text="Too short.")], TOPICS["business"]) == []


def test_short_passage_labels_map_back_and_form_names_are_not_figures():
    p = _passage()
    para = {"text": "The company's 10-K describes its software business (Item 1).",
            "quotes": [{"passage_id": "[P1]", "quote": "We design and sell analytics software to enterprise customers"}]}
    provider = ScriptedProvider(lambda *a: tool_turn(("submit_section", {"paragraphs": [para]})))
    paras, notes = draft_topic([p], TOPICS["business"], "Demo", provider)
    assert not notes and paras[0].passage_ids == [p.id]


def test_a_research_checkpoint_from_an_older_schema_is_ignored(demo, store):
    run, _ = demo
    with store.tx() as c:
        c.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?)",
                  (run.id, "research", "2026-01-01", '{"recommendation": {"rating": "SELL"}}'))
    assert load_results(store, run.id)["research"] is None
