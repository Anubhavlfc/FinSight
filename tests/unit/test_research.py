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
from finsight.research.models import MarketSnapshot, Valuation
from finsight.research.narrative import TOPICS, draft_topic, extract
from finsight.research.service import generate, manual_snapshot, render
from finsight.research.valuation import ValuationSettings, dcf_value, load_valuation_settings, rate, value
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


# --------------------------------------------------------------------------- valuation arithmetic


def test_dcf_matches_a_hand_calculation():
    # Two years, flat 10% growth then fading to 2%, r = 10%, no net cash, 1 share.
    per_share, rows, pv, tv, pv_tv = dcf_value(D(100), D(10), D(2), D(10), 2, D(0), D(1))
    fcf1, fcf2 = D(110), D(110) * D("1.02")
    exp_pv = fcf1 / D("1.1") + fcf2 / D("1.21")
    exp_tv = fcf2 * D("1.02") / D("0.08")
    assert [r.growth_pct for r in rows] == [D(10), D(2)]
    assert abs(pv - exp_pv) < D("1e-18") and abs(tv - exp_tv) < D("1e-18")
    assert abs(per_share - (exp_pv + exp_tv / D("1.21"))) < D("1e-18")


def test_discount_rate_must_exceed_terminal_growth():
    with pytest.raises(ValueError):
        dcf_value(D(100), D(5), D(9), D(9), 5, D(0), D(1))


def test_config_assumptions_are_loaded_and_capm_rate_is_derived(settings):
    vs = load_valuation_settings(settings.config_dir)
    assert vs.risk_free is not None and vs.risk_free.source_url
    assert vs.discount_rate_pct == vs.risk_free.value_pct + vs.beta * vs.equity_risk_premium_pct
    assert vs.base_fcf_method in ("average_margin", "latest")


@pytest.mark.parametrize("upside,expected", [(D("10.0"), "BUY"), (D("9.99"), "HOLD"), (D("-9.99"), "HOLD"),
                                             (D("-10.0"), "SELL")])
def test_rating_rule_thresholds(upside, expected):
    v = Valuation(available=True, target_price=D(1), upside_pct=upside)
    assert rate(v, _snap(), ValuationSettings()).rating == expected


def test_no_price_means_not_rated():
    v = Valuation(available=True, target_price=D(1), upside_pct=D(50))
    assert rate(v, None, ValuationSettings()).rating == "NOT RATED"


def _metric(name, period, val, unit="USD"):
    return CalculatedMetric(id=f"metric:{name}:{period}", name=name, fiscal_period=period, value=D(val), unit=unit,
                            formula="test")


def test_non_positive_free_cash_flow_is_not_valued():
    labels = ["FY1", "FY2"]
    ms = [_metric("revenue", "FY1", 100), _metric("revenue", "FY2", 110), _metric("free_cash_flow", "FY1", 5),
          _metric("free_cash_flow", "FY2", -20), _metric("cash_and_equivalents", "FY2", 10),
          _metric("interest_bearing_debt", "FY2", 0)]
    vs = ValuationSettings(risk_free=None, discount_rate_override_pct=D(9), base_fcf_method="latest")
    v, _ = value(ms, labels, D(10), None, _snap(), vs)
    assert not v.available and "not positive" in v.reason


def test_missing_inputs_are_named_instead_of_guessed():
    labels = ["FY1", "FY2"]
    ms = [_metric("revenue", "FY1", 100), _metric("revenue", "FY2", 110), _metric("free_cash_flow", "FY2", 20)]
    v, _ = value(ms, labels, None, None, _snap(), ValuationSettings(discount_rate_override_pct=D(9),
                                                                   base_fcf_method="latest"))
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
    assert rep.recommendation.rating in ("BUY", "HOLD", "SELL")
    assert [s.title for s in rep.sections] == SECTIONS
    assert rep.valuation.available and rep.valuation.target_price is not None
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
    for label in ("Company name", "Exchange", "Ticker symbol", "Sector", "Industry", "Recommendation",
                  "Current price", "Target price", "as of 02 November 2026"):
        assert label in flat, label
    for title in SECTIONS:
        assert title in flat, title
    assert "front cover goes here" in flat and "SYNTHETIC DEMO DATA" in flat
    assert "DRAFT · NOT APPROVED" in flat and "Not investment advice" in flat
    assert f"{rep.valuation.target_price:,.2f}" in flat


def test_without_a_price_the_report_is_unrated_but_still_renders(demo, settings, store, tmp_path):
    run, results = demo
    rep = generate(store, settings, run, results, None, FixtureSource(DEMO_FIXTURES), use_llm=False)
    assert rep.recommendation.rating == "NOT RATED"
    results["research"] = rep
    assert render(rep, results, tmp_path / "x.pdf").body_pages <= 10


def test_price_for_another_ticker_is_refused(demo, settings, store):
    run, results = demo
    with pytest.raises(ResearchError):
        generate(store, settings, run, results, _snap(ticker="MSFT"), FixtureSource(DEMO_FIXTURES), use_llm=False)


def test_unfinished_run_is_refused(demo, settings):
    run, results = demo
    with pytest.raises(ResearchError):
        build_research(run.model_copy(update={"state": RunState.COLLECTING}), results, None, ValuationSettings(), None, None)


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
