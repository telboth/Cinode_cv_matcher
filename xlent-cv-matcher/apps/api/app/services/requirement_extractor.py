from __future__ import annotations

import re

from app.models.requirement import Requirement


KEYWORD_WEIGHTS = {
    "må": ("must", 0.9),
    "must": ("must", 0.9),
    "required": ("must", 0.9),
    "skal": ("must", 0.9),
    "bør": ("should", 0.65),
    "should": ("should", 0.65),
    "fordel": ("nice_to_have", 0.4),
    "nice to have": ("nice_to_have", 0.4),
}


def _infer_category(line: str) -> tuple[str, float]:
    lowered = line.lower()
    for key, value in KEYWORD_WEIGHTS.items():
        if key in lowered:
            return value
    return ("should", 0.55)


def extract_requirements(opportunity_id: str, text: str) -> list[Requirement]:
    candidates: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        is_bullet = bool(re.match(r"^[-*•]\s+", line))
        is_requirement_sentence = any(
            token in line.lower()
            for token in ["krav", "må", "skal", "must", "required", "should", "bør", "erfaring"]
        )

        if is_bullet or is_requirement_sentence:
            cleaned = re.sub(r"^[-*•]\s+", "", line)
            candidates.append(cleaned)

    unique_candidates = list(dict.fromkeys(candidates))[:20]

    return [
        Requirement(
            opportunity_id=opportunity_id,
            category=_infer_category(item)[0],
            text=item,
            weight=_infer_category(item)[1],
            extracted_by="ai",
        )
        for item in unique_candidates
    ]
