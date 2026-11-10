"""10-K text extraction, sections, chunking, quote verification and search (SYNTHETIC text)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from finsight.contracts import SourceDocument, SourceKind
from finsight.evidence.search import PassageIndex
from finsight.evidence.text import extract_passages, find_sections, html_to_text, quote_in_passage

DOC = SourceDocument(id="src:test", kind=SourceKind.FILING_DOCUMENT, cik="0000000001", url="test://10k",
                     retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), sha256="0" * 64, is_fixture=True)

HTML = """<html><head><title>x</title><style>p{}</style></head><body>
<ix:header><p>hidden XBRL header Item 7. should not count</p></ix:header>
<p>TABLE OF CONTENTS</p><p>Item 1. Business</p><p>Item 7. Management's Discussion</p><p>Item 8. Financial Statements</p>
<p>Item 1. Business</p><p>We make widgets.</p>
<p>Item 7.</p><p>MANAGEMENT'S DISCUSSION AND ANALYSIS</p>
<p>Revenue increased $50 million, driven
   primarily by higher widget volumes.</p>
<p>Item 7</p>
<p>Operating expenses increased $10 million due to hiring.</p>
<p>Item 8. Financial Statements and Supplementary Data</p>
<p>NOTE 1 — ACCOUNTING POLICIES</p><p>We follow US GAAP.</p>
<p>NOTE 9 — DEBT</p><p>We issued $480 million of senior notes.</p>
<p>Item 9. Changes in Accountants</p><p>None.</p>
</body></html>"""


def passages():
    ps, notes = extract_passages(HTML, DOC, accession="0000000001-26-000001", form="10-K",
                                 filing_date=date(2026, 2, 1), fiscal_period="FY2025", max_chars=400)
    return ps, notes


def test_source_line_breaks_are_collapsed_and_hidden_header_dropped():
    text = html_to_text(HTML)
    assert "driven primarily by higher widget volumes." in text
    assert "hidden XBRL header" not in text


def test_table_of_contents_and_running_headers_do_not_split_sections():
    ps, notes = passages()
    assert notes == []
    mdna = " ".join(p.text for p in ps if p.section == "item_7")
    # The TOC line is skipped; the running "Item 7" page header does not end the section.
    assert "Revenue increased $50 million" in mdna and "Operating expenses increased $10 million" in mdna
    assert {p.section for p in ps} >= {"item_1", "item_7", "item_8/note_1", "item_8/note_9"}
    assert not any(p.section == "item_9" for p in ps)  # not a kept section


def test_passages_keep_offsets_into_extracted_text():
    text = html_to_text(HTML)
    for p in passages()[0]:
        assert text[p.char_start:p.char_end] == p.text
        assert p.id.startswith("psg:0000000001-26-000001:")
        assert p.is_fixture and p.url == "test://10k"


def test_item_heading_with_title_on_next_line():
    secs = {s.key: s for s in find_sections(html_to_text(HTML))}
    assert "MANAGEMENT" in secs["item_7"].title.upper()


def test_quote_verification_is_strict_about_content():
    note9 = next(p for p in passages()[0] if p.section == "item_8/note_9")
    assert quote_in_passage("We issued $480 million of senior notes.", note9)[0]
    assert quote_in_passage("we  issued $480 million\nof senior notes", note9)[0]  # whitespace/case only
    assert not quote_in_passage("We issued $490 million of senior notes.", note9)[0]  # changed number
    assert not quote_in_passage("$480 million", note9)[0]  # too short to be evidence


def test_search_ranks_relevant_passage_and_filters_sections():
    idx = PassageIndex(passages()[0])
    top = idx.search("notes issued debt")[0][0]
    assert top.section == "item_8/note_9"
    assert all(p.section == "item_7" for p, _ in idx.search("increased", sections=["item_7"]))
    assert idx.search("increased", fiscal_period="FY2099") == []
    assert all(p.section.startswith("item_8") for p, _ in idx.search("notes", sections=["item_8"]))
