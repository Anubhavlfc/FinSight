"""Narrative sections drawn from the company's 10-K text.

Two modes, both cite every statement to a filing passage:

* ``ai_summary`` - Claude writes short paragraphs from the passages it is given and must back each
  paragraph with verbatim quotes. A paragraph is dropped unless at least one quote is found verbatim
  in the cited passage, and dropped if it states a number that none of its quotes contains.
* ``filing_extract`` - no model: the leading sentences of the best-matching passages are quoted
  verbatim. Used offline, without an API key, or when the model fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from finsight.contracts import FilingPassage
from finsight.evidence.search import PassageIndex
from finsight.evidence.text import normalise_for_match, quote_in_passage
from finsight.llm.provider import LLMError, LLMProvider, ToolSpec


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    sections: tuple[str, ...]
    queries: tuple[str, ...]
    brief: str
    max_paragraphs: int = 3


TOPICS = {
    "business": Topic("business", "Business Description", ("item_1",),
                      ("company overview mission products services", "segments segment reporting revenue",
                       "customers distribution sales channels"),
                      "What the company does, how it is organised (segments) and how it sells.", 3),
    "industry": Topic("industry", "Industry Overview and Competitive Positioning", ("item_1", "item_1a"),
                      ("competition competitive compete competitors", "market industry rapidly evolving technology",
                       "competitive advantage scale brand ecosystem innovation"),
                      "The markets the company competes in, who it competes with, and what it says its "
                      "competitive strengths and pressures are.", 3),
    "risks": Topic("risks", "Investment Risks", ("item_1a",),
                   ("competition competitors pricing pressure", "government regulation legal antitrust",
                    "cybersecurity security breach data", "economic conditions demand macroeconomic",
                    "supply chain manufacturing components", "artificial intelligence AI development risks",
                    "international operations foreign exchange currency"),
                   "The most material business risks the company itself discloses.", 5),
    "esg": Topic("esg", "Environmental, Social and Governance", ("item_1", "item_1a"),
                 ("climate sustainability carbon emissions renewable energy environmental",
                  "employees human capital workforce culture inclusion",
                  "governance board ethics compliance oversight"),
                 "What the 10-K says on environmental commitments, people (human capital) and governance.", 3),
}


class _Quote(BaseModel):
    passage_id: str
    quote: str = Field(description="Exact text copied from the passage, at least 20 characters")


class _Para(BaseModel):
    text: str = Field(description="One paragraph, at most 90 words, no numbers unless they appear in a quote")
    quotes: list[_Quote] = Field(min_length=1)


class SubmitSectionIn(BaseModel):
    paragraphs: list[_Para]


SUBMIT = ToolSpec("submit_section", "Submit the section's paragraphs with their supporting quotes.", SubmitSectionIn)

SYSTEM = (
    "You are an independent equity research analyst drafting one section of a research report. Use only the "
    "10-K passages provided. Write plain, neutral prose; no recommendation, no price talk, no promotional "
    "language. Every paragraph must be supported by one or more quotes copied exactly from the passages, with "
    "the passage ID. Do not state any number, percentage or year that does not appear in one of that "
    "paragraph's quotes. Attribute company claims to the company (\"the company says\", \"Microsoft "
    "describes\"). Call submit_section exactly once.")

_NUM = re.compile(r"\d[\d,.]*")
# Document names are not figures: "10-K", "Form 8-K", "Item 1A".
_FORM_REFS = re.compile(r"\b(?:Form\s+)?(?:10-K|10-Q|8-K|20-F|DEF\s+14A)\b|\bItem\s+\d+[A-C]?\b", re.IGNORECASE)


@dataclass
class DraftedParagraph:
    text: str
    passage_ids: list[str]
    kind: str  # ai_summary | filing_extract


def select_passages(passages: list[FilingPassage], topic: Topic, period: str | None, per_query: int = 3,
                    limit: int = 10) -> list[FilingPassage]:
    pool = [p for p in passages if p.section.split("/")[0] in topic.sections
            and (period is None or p.fiscal_period == period)]
    if not pool:
        return []
    idx = PassageIndex(pool)
    out: list[FilingPassage] = []
    seen: set[str] = set()
    for q in topic.queries:
        for p, _score in idx.search(q, limit=per_query):
            if p.id not in seen:
                seen.add(p.id)
                out.append(p)
    return out[:limit]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", " ".join(text.split()))
    return [s for s in parts if len(s) >= 40]


def extract(passages: list[FilingPassage], topic: Topic) -> list[DraftedParagraph]:
    """Quote the leading sentences of the top passages, one quote per query theme."""
    out = []
    for p in passages:
        sents = _sentences(p.text)
        if not sents:
            continue
        quote = sents[0]
        if len(quote) < 160 and len(sents) > 1:
            quote = f"{quote} {sents[1]}"
        if len(quote) > 420:
            quote = quote[:420].rsplit(" ", 1)[0] + " …"
        ok, _ = quote_in_passage(quote.rstrip(" …"), p, 20)
        if not ok:
            continue
        out.append(DraftedParagraph(f"“{quote}”", [p.id], "filing_extract"))
        if len(out) >= topic.max_paragraphs:
            break
    return out


def summarise(passages: list[FilingPassage], topic: Topic, company: str, provider: LLMProvider,
              ) -> tuple[list[DraftedParagraph], list[str]]:
    """Claude-drafted paragraphs; returns (paragraphs kept, notes on anything dropped)."""
    # Short labels (P1, P2, ...) are easier for the model to copy exactly than long passage IDs.
    by_id = {f"P{i}": p for i, p in enumerate(passages, 1)}
    by_id.update({p.id: p for p in passages})
    body = "\n\n".join(f"[P{i}] ({p.section_title}, {p.form} filed {p.filing_date})\n{p.text}"
                       for i, p in enumerate(passages, 1))
    prompt = (f"Company: {company}\nSection: {topic.title}\nWhat this section covers: {topic.brief}\n"
              f"Write at most {topic.max_paragraphs} paragraphs. Give each quote's passage_id as its label, "
              f"e.g. P3.\n\nPassages:\n{body}")
    session = provider.start(f"research_{topic.key}", SYSTEM, [SUBMIT], context={"topic": topic.key})
    notes: list[str] = []
    turn = session.send(prompt)
    call = next((c for c in turn.tool_calls if c.name == SUBMIT.name), None)
    if call is None:  # one bounded retry
        from finsight.llm.provider import ToolResult
        results = [ToolResult(c.id, "Only submit_section is available.", True) for c in turn.tool_calls]
        turn = session.send(results or "Call submit_section now.")
        call = next((c for c in turn.tool_calls if c.name == SUBMIT.name), None)
    if call is None:
        raise LLMError("model did not submit the section")
    data = SubmitSectionIn.model_validate(call.input)
    kept: list[DraftedParagraph] = []
    for para in data.paragraphs[: topic.max_paragraphs]:
        for q in para.quotes:
            q.passage_id = q.passage_id.strip().strip("[]")
        good = [q for q in para.quotes if q.passage_id in by_id and quote_in_passage(q.quote, by_id[q.passage_id], 20)[0]]
        if not good:
            notes.append(f"{topic.title}: a paragraph was dropped because none of its quotes matched the filing.")
            continue
        quoted = " ".join(normalise_for_match(q.quote) for q in good)
        stray = [n for n in _NUM.findall(_FORM_REFS.sub(" ", para.text))
                 if n.strip(".,") and n.strip(".,") not in quoted]
        if stray:
            notes.append(f"{topic.title}: a paragraph was dropped because it stated figures not in its quotes "
                         f"({', '.join(stray[:3])}).")
            continue
        kept.append(DraftedParagraph(" ".join(para.text.split()),
                                     list(dict.fromkeys(by_id[q.passage_id].id for q in good)),
                                     "ai_summary"))
    return kept, notes


def draft_topic(passages: list[FilingPassage], topic: Topic, company: str, provider: LLMProvider | None,
                ) -> tuple[list[DraftedParagraph], list[str]]:
    if not passages:
        return [], [f"{topic.title}: no matching 10-K passages were loaded, so this section has no filing text."]
    if provider is not None and not provider.is_stub:
        try:
            kept, notes = summarise(passages, topic, company, provider)
            if kept:
                return kept, notes
            notes.append(f"{topic.title}: no AI paragraph survived verification; filing extracts used instead.")
            return extract(passages, topic), notes
        except (LLMError, ValueError) as exc:
            return extract(passages, topic), [f"{topic.title}: AI drafting failed ({exc.__class__.__name__}); "
                                             "filing extracts used instead."]
    return extract(passages, topic), []


__all__ = ["TOPICS", "Topic", "select_passages", "draft_topic"]
