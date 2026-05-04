from __future__ import annotations

import re
from typing import Any

from app.models.cv_suggestion import CvSuggestion


def _norm(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip().lower())
    value = re.sub(r"[^a-z0-9æøå\s|:+#./-]", "", value)
    return value


def _looks_like_same_section(a: str, b: str) -> bool:
    a_norm = _norm(a)
    b_norm = _norm(b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True
    shorter, longer = (a_norm, b_norm) if len(a_norm) <= len(b_norm) else (b_norm, a_norm)
    if len(shorter) < 80:
        return False
    return shorter in longer


def build_cinode_payload(
    base_payload: dict[str, Any],
    suggestions: list[CvSuggestion],
    *,
    include_pending: bool = False,
) -> dict[str, Any]:
    payload = dict(base_payload)

    accepted_states = {"accepted"}
    if include_pending:
        accepted_states.add("pending")
    accepted = [s for s in suggestions if str(s.status or "").strip().lower() in accepted_states]
    tailored_sections: list[dict[str, str]] = []
    skill_like_types = {"skills", "tools", "techniques", "teknikker", "verktøy", "verktoy", "kompetanse"}
    keyword_types = {"keyword", "tool_keyword", "technique_keyword"}
    skills_list: list[str] = []
    summary_updated = False
    base_summary = str(payload.get("summary") or "").strip()
    fallback_summary_candidates: list[CvSuggestion] = []
    current_skills = payload.get("skills")
    if isinstance(current_skills, list):
        skills_list = [str(item).strip() for item in current_skills if str(item).strip()]
    keyword_terms: list[str] = []

    for item in accepted:
        section_type = str(item.section_type or "").strip().lower()
        if section_type == "summary":
            payload["summary"] = item.suggested_text
            summary_updated = True
        elif str(item.section_type or "").strip().lower() in skill_like_types:
            skills_list = [s.strip() for s in item.suggested_text.split(",") if s.strip()]
        elif section_type in keyword_types:
            keyword = str(item.suggested_text or "").strip()
            if keyword:
                keyword_terms.append(keyword)
        else:
            tailored_sections.append(
                {
                    "section_type": str(item.section_type or "other"),
                    "suggested_text": str(item.suggested_text or "").strip(),
                }
            )
            if base_summary:
                original_text = str(item.original_text or "").strip()
                suggested_text = str(item.suggested_text or "").strip()
                if suggested_text and _looks_like_same_section(original_text, base_summary):
                    fallback_summary_candidates.append(item)

    if not summary_updated and fallback_summary_candidates:
        # If the model returned the summary rewrite with an unexpected section_type
        # (e.g. "other"), still use it as summary when it matches the original summary.
        payload["summary"] = str(fallback_summary_candidates[0].suggested_text or "").strip()

    if keyword_terms:
        merged: list[str] = []
        seen: set[str] = set()
        for value in [*skills_list, *keyword_terms]:
            key = value.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(value.strip())
        skills_list = merged

    if skills_list:
        payload["skills"] = skills_list

    if tailored_sections:
        payload["tailored_sections"] = tailored_sections

    return payload
