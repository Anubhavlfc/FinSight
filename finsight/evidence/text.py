"""Turn a 10-K primary document into section-aware, citable passages.

Approach (deliberately simple, see docs/architecture.md):

1. HTML -> plain text, one line per block element. Inline XBRL tags are just HTML
   to this step; hidden ``ix:header`` content is dropped.
2. Find ``Item N.`` headings at the start of a line. A 10-K repeats every heading
   in its table of contents, so for each Item we keep the occurrence that starts
   the longest stretch of text (the real section, not the TOC line).
3. Inside Item 8, split on ``Note N — Title`` headings so notes (debt, segments)
   can be searched on their own.
4. Chunk each section into passages of whole paragraphs (about ``max_chars``),
   keeping character offsets into the extracted text so every passage can be
   located again in the source.

Passage text is filing content: untrusted data that is shown to the LLM only
inside clearly delimited tool results.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from finsight.contracts import FilingPassage, SourceDocument

SECTION_TITLES = {
    "item_1": "Item 1. Business",
    "item_1a": "Item 1A. Risk Factors",
    "item_7": "Item 7. Management's Discussion and Analysis (MD&A)",
    "item_7a": "Item 7A. Market Risk",
    "item_8": "Item 8. Financial Statements and Notes",
}
# Sections kept for investigation. Others (properties, legal, exhibits...) are dropped.
KEEP = set(SECTION_TITLES)

_ITEM_RE = re.compile(r"^\s*item\s*(\d{1,2}[a-c]?)\s*[.:\-–—]?\s*(.{0,160})$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^\s*note\s+(\d{1,2})\s*[.:\-–—]+\s*(.{2,120})$", re.IGNORECASE)
_BLOCK_TAGS = ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "br", "td")


def html_to_text(html: bytes | str) -> str:
    """Plain text with one line per block element and normalised whitespace."""
    from bs4 import BeautifulSoup  # imported lazily: only needed for investigation

    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    import warnings

    from bs4 import XMLParsedAsHTMLWarning

    with warnings.catch_warnings():  # inline XBRL documents are XHTML; the HTML parser handles them fine
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for tag in soup.find_all(["ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        tag.decompose()
    # Line breaks inside the HTML source are just whitespace; only block elements start lines.
    from bs4 import NavigableString

    for node in soup.find_all(string=True):
        if "\n" in node or "\r" in node:
            node.replace_with(NavigableString(re.sub(r"[\r\n]+", " ", str(node))))
    # td cells become " | " separated so table rows stay on one line.
    for td in soup.find_all("td"):
        td.append(" | ")
    for tag in soup.find_all(_BLOCK_TAGS):
        if tag.name not in ("td",):
            tag.insert_before("\n")
            tag.insert_after("\n")
    text = soup.get_text()
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        line = re.sub(r"(\s*\|\s*)+$", "", line)
        line = re.sub(r"^(\|\s*)+", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


@dataclass
class Section:
    key: str
    title: str
    start: int  # offset of the first character after the heading line
    end: int


def _line_offsets(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for line in text.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def find_sections(text: str) -> list[Section]:
    lines = _line_offsets(text)
    heads: list[tuple[str, str, int, int]] = []  # key, title, heading_start, body_start
    for i, (pos, line) in enumerate(lines):
        m = _ITEM_RE.match(line)
        if not m or len(line) > 200:
            continue
        key = f"item_{m.group(1).lower()}"
        title = m.group(2).strip()
        if not title and i + 1 < len(lines):  # "Item 7." alone on its line, title on the next
            title = lines[i + 1][1][:160]
        if heads and heads[-1][0] == key:
            # Running page headers ("Item 7" at the top of every page) repeat the current
            # item; only a change of item starts a new section.
            continue
        heads.append((key, title, pos, pos + len(line) + 1))
    best: dict[str, Section] = {}
    for n, (key, title, _hpos, body) in enumerate(heads):
        end = heads[n + 1][2] if n + 1 < len(heads) else len(text)
        if key not in best or (end - body) > (best[key].end - best[key].start):
            best[key] = Section(key=key, title=f"Item {key[5:].upper()}. {title}".strip(), start=body, end=end)
    return sorted(best.values(), key=lambda s: s.start)


def split_notes(text: str, section: Section) -> list[Section]:
    """Split Item 8 into notes. Text before the first note stays as item_8."""
    body = text[section.start:section.end]
    lines = _line_offsets(body)
    heads = []
    for pos, line in lines:
        m = _NOTE_RE.match(line)
        if m and len(line) < 140:
            heads.append((f"{section.key}/note_{int(m.group(1))}", f"Note {int(m.group(1))}. {m.group(2).strip()}",
                          section.start + pos, section.start + pos + len(line) + 1))
    if not heads:
        return [section]
    # Notes are often repeated (e.g. in an index); keep the longest occurrence per note.
    best: dict[str, Section] = {}
    for n, (key, title, _h, body_start) in enumerate(heads):
        end = heads[n + 1][2] if n + 1 < len(heads) else section.end
        if key not in best or (end - body_start) > (best[key].end - best[key].start):
            best[key] = Section(key, title, body_start, end)
    out = [Section(section.key, section.title, section.start, heads[0][2])] if heads[0][2] > section.start else []
    return out + sorted(best.values(), key=lambda s: s.start)


def chunk(text: str, section: Section, max_chars: int) -> list[tuple[int, int]]:
    """Paragraph-aligned (start, end) offsets, each at most about ``max_chars`` long."""
    spans: list[tuple[int, int]] = []
    pos = section.start
    cur_start, cur_end = None, None
    for para in text[section.start:section.end].split("\n"):
        p_start, p_end = pos, pos + len(para)
        pos = p_end + 1
        if not para.strip():
            continue
        if len(para) > max_chars:  # very long paragraph: hard-split on sentence boundaries
            if cur_start is not None:
                spans.append((cur_start, cur_end))
                cur_start = None
            s = p_start
            while s < p_end:
                e = min(s + max_chars, p_end)
                if e < p_end:
                    dot = text.rfind(". ", s, e)
                    e = dot + 1 if dot > s + max_chars // 2 else e
                spans.append((s, e))
                s = e
                while s < p_end and text[s] == " ":
                    s += 1
            continue
        if cur_start is None:
            cur_start, cur_end = p_start, p_end
        elif p_end - cur_start <= max_chars:
            cur_end = p_end
        else:
            spans.append((cur_start, cur_end))
            cur_start, cur_end = p_start, p_end
    if cur_start is not None:
        spans.append((cur_start, cur_end))
    return spans


def extract_passages(html: bytes | str, doc: SourceDocument, *, accession: str, form: str, filing_date,
                     fiscal_period: str | None, max_chars: int = 1500) -> tuple[list[FilingPassage], list[str]]:
    """Returns passages for the kept sections, plus notes about anything that could not be found."""
    text = html_to_text(html)
    notes: list[str] = []
    sections = find_sections(text)
    found = {s.key for s in sections}
    for key in ("item_7", "item_8"):
        if key not in found:
            notes.append(f"{form} {accession}: could not locate {SECTION_TITLES[key]}")
    passages: list[FilingPassage] = []
    for sec in sections:
        if sec.key not in KEEP:
            continue
        subs = split_notes(text, sec) if sec.key == "item_8" else [sec]
        for sub in subs:
            title = SECTION_TITLES.get(sub.key, sub.title) if "/" not in sub.key else sub.title
            for n, (a, b) in enumerate(chunk(text, sub, max_chars)):
                passages.append(FilingPassage(
                    id=f"psg:{accession}:{sub.key}:{n:03d}", accession=accession, form=form,
                    filing_date=filing_date, fiscal_period=fiscal_period, section=sub.key, section_title=title,
                    text=text[a:b], char_start=a, char_end=b, source_document_id=doc.id, url=doc.url,
                    is_fixture=doc.is_fixture))
    if not passages:
        notes.append(f"{form} {accession}: no usable text sections found")
    return passages, notes


def normalise_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_passage(quote: str, passage: FilingPassage, min_chars: int = 20) -> tuple[bool, str]:
    """Deterministic check that ``quote`` appears verbatim (modulo whitespace/quote style) in the passage."""
    q = normalise_for_match(quote).strip(" .\"'")
    if len(q) < min_chars:
        return False, f"quote shorter than {min_chars} characters"
    if q in normalise_for_match(passage.text):
        return True, "verbatim match"
    return False, "quote not found verbatim in the cited passage"
