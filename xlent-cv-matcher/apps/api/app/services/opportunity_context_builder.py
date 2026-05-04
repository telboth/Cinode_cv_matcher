from __future__ import annotations

import re
from typing import Any

from app.models.requirement import Requirement


_STOPWORDS = {
    "og",
    "i",
    "på",
    "for",
    "til",
    "av",
    "med",
    "the",
    "and",
    "with",
    "from",
    "som",
    "eller",
    "må",
    "skal",
    "bør",
    "nice",
    "have",
    "krav",
    "erfaring",
}


def _clean_text(value: Any, limit: int = 3000) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _extract_keywords(text: str, limit: int = 24) -> list[str]:
    tokens = re.findall(r"[A-Za-zÆØÅæøå0-9+#.-]{3,}", text)
    scored: dict[str, int] = {}
    for token in tokens:
        key = token.lower()
        if key in _STOPWORDS:
            continue
        scored[key] = scored.get(key, 0) + 1

    ordered = sorted(scored.items(), key=lambda x: (-x[1], x[0]))
    out: list[str] = []
    for key, _ in ordered:
        out.append(key)
        if len(out) >= limit:
            break
    return out


def build_opportunity_context(title: str, source_text: str, requirements: list[Requirement]) -> dict[str, Any]:
    sorted_requirements = sorted(requirements, key=lambda r: float(r.weight), reverse=True)
    must = [r for r in sorted_requirements if r.category == "must"]
    should = [r for r in sorted_requirements if r.category == "should"]
    nice = [r for r in sorted_requirements if r.category == "nice_to_have"]

    weighted_requirements = [
        {"category": r.category, "text": _clean_text(r.text, 400), "weight": float(r.weight)} for r in sorted_requirements[:15]
    ]
    must_texts = [_clean_text(r.text, 240) for r in must[:8]]
    should_texts = [_clean_text(r.text, 240) for r in should[:8]]
    nice_texts = [_clean_text(r.text, 240) for r in nice[:8]]

    title_clean = _clean_text(title, 220)
    source_excerpt = _clean_text(source_text, 4000)
    keyword_input = " ".join([title_clean, source_excerpt, " ".join(req["text"] for req in weighted_requirements)])
    keywords = _extract_keywords(keyword_input, limit=30)

    return {
        "title": title_clean,
        "source_excerpt": source_excerpt,
        "prioritized_requirements": weighted_requirements,
        "requirement_groups": {
            "must": must_texts,
            "should": should_texts,
            "nice_to_have": nice_texts,
        },
        "keyword_hints": keywords,
        "selection_logic": {
            "must_count": len(must),
            "should_count": len(should),
            "nice_to_have_count": len(nice),
        },
    }
