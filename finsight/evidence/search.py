"""Keyword search over filing passages (BM25).

Section-aware chunks plus plain keyword ranking are the Stage 2 baseline. Add
embeddings only if evaluation shows this misses evidence the investigator needs.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from finsight.contracts import FilingPassage

_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "by", "with", "as", "at", "our", "we",
         "is", "was", "were", "be", "are", "from", "that", "this", "its", "it", "which", "than", "fiscal", "year"}


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        if t in _STOP:
            continue
        # crude stemming so "increased"/"increases"/"increase" match
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > 5 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


class PassageIndex:
    def __init__(self, passages: list[FilingPassage], k1: float = 1.4, b: float = 0.75):
        self.passages = {p.id: p for p in passages}
        self._order = [p.id for p in passages]
        self._tf = {p.id: Counter(tokens(p.text + " " + p.section_title)) for p in passages}
        self._len = {pid: sum(c.values()) for pid, c in self._tf.items()}
        self._avg = (sum(self._len.values()) / len(self._len)) if self._len else 1.0
        df: Counter = Counter()
        for c in self._tf.values():
            df.update(c.keys())
        n = len(passages)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def add(self, passages: list[FilingPassage]) -> None:
        merged = list(self.passages.values()) + [p for p in passages if p.id not in self.passages]
        self.__init__(merged, self.k1, self.b)

    def get(self, passage_id: str) -> FilingPassage | None:
        return self.passages.get(passage_id)

    def accessions(self) -> set[str]:
        return {p.accession for p in self.passages.values()}

    def search(self, query: str, *, sections: list[str] | None = None, fiscal_period: str | None = None,
               accession: str | None = None, limit: int = 5) -> list[tuple[FilingPassage, float]]:
        q = tokens(query)
        if not q:
            return []
        scored = []
        for pid in self._order:
            p = self.passages[pid]
            if sections and not any(p.section == s or p.section.startswith(s + "/") for s in sections):
                continue
            if fiscal_period and p.fiscal_period != fiscal_period:
                continue
            if accession and p.accession != accession:
                continue
            tf = self._tf[pid]
            score = 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self._len[pid] / self._avg)
                score += self._idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            if score > 0:
                scored.append((p, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]
