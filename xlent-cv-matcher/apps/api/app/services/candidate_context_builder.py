from __future__ import annotations

import re
from typing import Any


_BLOCK_TYPE_MAP = {
    9: "summary",
    12: "skills",
    6: "experience",
    7: "education",
    8: "certifications",
    14: "publications",
}


def _clean_text(value: Any, limit: int = 1400) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def _flatten_text(value: Any, depth: int = 0) -> str:
    if depth > 3:
        return ""
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value[:30]:
            part = _flatten_text(item, depth + 1)
            if part:
                parts.append(part)
        return _clean_text(" | ".join(parts), limit=1800)
    if isinstance(value, dict):
        parts: list[str] = []
        interesting_keys = ["title", "heading", "name", "description", "summary", "text", "role", "company", "school"]
        for key in interesting_keys:
            if key in value:
                part = _flatten_text(value.get(key), depth + 1)
                if part:
                    parts.append(part)
        if not parts:
            for _, child in list(value.items())[:12]:
                part = _flatten_text(child, depth + 1)
                if part:
                    parts.append(part)
        return _clean_text(" | ".join(parts), limit=1800)
    return _clean_text(value)


def _extract_resume_blocks(profile_payload: dict[str, Any]) -> list[dict[str, Any]]:
    source_resume = profile_payload.get("source_resume")
    if not isinstance(source_resume, dict):
        return []
    resume = source_resume.get("resume")
    if not isinstance(resume, dict):
        return []
    blocks = resume.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def _extract_summary(profile_payload: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    # Summary comes from Cinode "Tittel og sammendrag" and can be long.
    summary = _clean_text(profile_payload.get("summary"), limit=12000)
    if summary:
        return summary

    for block in blocks:
        if block.get("blockType") == 9:
            text = _clean_text(block.get("description"), limit=12000)
            if text:
                return text
    return ""


def _extract_skills(profile_payload: dict[str, Any], blocks: list[dict[str, Any]]) -> list[str]:
    top_level_skills = profile_payload.get("skills")
    if isinstance(top_level_skills, list):
        from_top = [_clean_text(item, limit=80) for item in top_level_skills if _clean_text(item, limit=80)]
        if from_top:
            return list(dict.fromkeys(from_top))[:80]

    skills: list[str] = []
    for block in blocks:
        if block.get("blockType") != 12:
            continue
        data = block.get("data")
        if not isinstance(data, list):
            continue
        for category in data:
            if not isinstance(category, dict):
                continue
            entries = category.get("skills")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = _clean_text(entry.get("name"), limit=80)
                if name:
                    skills.append(name)
    return list(dict.fromkeys(skills))[:80]


def _append_section(
    sections: list[dict[str, Any]],
    section_type: str,
    text: str,
    source: str,
    max_change_percent: int = 20,
    limit: int = 2200,
) -> None:
    original_text = _clean_text(text, limit=limit)
    if not original_text:
        return
    sections.append(
        {
            "section_type": section_type,
            "original_text": original_text,
            "source": source,
            "max_change_percent": max_change_percent,
        }
    )


def build_cv_sections(profile_payload: dict[str, Any], max_change_percent_default: int = 20) -> list[dict[str, Any]]:
    blocks = _extract_resume_blocks(profile_payload)
    sections: list[dict[str, Any]] = []

    summary = _extract_summary(profile_payload, blocks)
    _append_section(
        sections,
        "summary",
        summary,
        source="cv_summary",
        max_change_percent=max_change_percent_default,
        limit=12000,
    )

    skills = _extract_skills(profile_payload, blocks)
    if skills:
        _append_section(
            sections,
            "skills",
            ", ".join(skills[:40]),
            source="cv_skills",
            max_change_percent=max_change_percent_default,
        )

    for block in blocks[:40]:
        raw_block_type = block.get("blockType")
        # Summary (9) and skills (12) are already added from dedicated extractors above.
        # Keeping them here creates near-duplicate sections in prompt payload.
        if raw_block_type in {9, 12}:
            continue
        section_type = _BLOCK_TYPE_MAP.get(raw_block_type, "other")
        text_parts = [
            _clean_text(block.get("title"), limit=180),
            _clean_text(block.get("description"), limit=1400),
            _flatten_text(block.get("data")),
        ]
        text = _clean_text(" | ".join(part for part in text_parts if part), limit=2200)
        if not text:
            continue
        _append_section(
            sections,
            section_type=section_type,
            text=text,
            source=f"resume_block_{raw_block_type}",
            max_change_percent=max_change_percent_default,
        )

    enrichment = profile_payload.get("enrichment")
    if isinstance(enrichment, dict):
        publications = enrichment.get("scholar_publications")
        if isinstance(publications, list):
            pubs = [_clean_text(p, limit=220) for p in publications if _clean_text(p, limit=220)]
            if pubs:
                _append_section(
                    sections,
                    "publications",
                    "; ".join(pubs[:10]),
                    source="scholar_publications",
                    max_change_percent=max_change_percent_default,
                )

    unique_sections: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for section in sections:
        key = (str(section["section_type"]), str(section["original_text"]))
        if key in seen:
            continue
        seen.add(key)
        unique_sections.append(section)
    return unique_sections[:20]


def build_candidate_context(profile_payload: dict[str, Any]) -> dict[str, Any]:
    blocks = _extract_resume_blocks(profile_payload)
    skills = _extract_skills(profile_payload, blocks)
    summary = _extract_summary(profile_payload, blocks)
    enrichment = profile_payload.get("enrichment")
    if not isinstance(enrichment, dict):
        enrichment = {}

    name = _clean_text(profile_payload.get("name"), limit=160)
    title = _clean_text(profile_payload.get("title"), limit=180)
    location = _clean_text(profile_payload.get("location"), limit=120)

    candidate_facts: list[str] = []
    if name:
        candidate_facts.append(f"Navn: {name}")
    if title:
        candidate_facts.append(f"Rolle/Tittel: {title}")
    if location:
        candidate_facts.append(f"Lokasjon: {location}")
    if skills:
        candidate_facts.append(f"Nøkkelkompetanse: {', '.join(skills[:12])}")
    if summary:
        candidate_facts.append(f"Kort profil: {_clean_text(summary, limit=260)}")

    enrichment_facts = enrichment.get("candidate_facts")
    if isinstance(enrichment_facts, list):
        for fact in enrichment_facts[:20]:
            cleaned = _clean_text(fact, limit=260)
            if cleaned:
                candidate_facts.append(cleaned)

    scholar_publications = enrichment.get("scholar_publications")
    scholar_publications_list: list[str] = []
    if isinstance(scholar_publications, list):
        scholar_publications_list = [_clean_text(item, limit=220) for item in scholar_publications if _clean_text(item, limit=220)][
            :12
        ]

    linkedin_url = _clean_text(enrichment.get("linkedin_url"), limit=500) if enrichment.get("linkedin_url") else None
    linkedin_profile_text = (
        _clean_text(enrichment.get("linkedin_profile_text"), limit=1200) if enrichment.get("linkedin_profile_text") else None
    )
    github_url = _clean_text(enrichment.get("github_url"), limit=500) if enrichment.get("github_url") else None
    orcid_url = _clean_text(enrichment.get("orcid_url"), limit=500) if enrichment.get("orcid_url") else None
    researchgate_url = _clean_text(enrichment.get("researchgate_url"), limit=500) if enrichment.get("researchgate_url") else None
    scholar_url = _clean_text(enrichment.get("scholar_url"), limit=500) if enrichment.get("scholar_url") else None
    external_findings = enrichment.get("external_findings") if isinstance(enrichment.get("external_findings"), list) else []
    sources = enrichment.get("sources") if isinstance(enrichment.get("sources"), list) else []

    return {
        "identity": {
            "name": name or None,
            "title": title or None,
            "location": location or None,
        },
        "skills": skills[:40],
        "summary": summary or None,
        "candidate_facts": list(dict.fromkeys(candidate_facts))[:30],
        "external_profiles": {
            "linkedin_url": linkedin_url,
            "linkedin_profile_text": linkedin_profile_text,
            "github_url": github_url,
            "orcid_url": orcid_url,
            "researchgate_url": researchgate_url,
            "scholar_url": scholar_url,
            "scholar_publications": scholar_publications_list,
            "external_findings": external_findings[:20],
        },
        "source_metadata": {
            "has_enrichment": bool(enrichment),
            "enrichment_sources": sources[:15] if isinstance(sources, list) else [],
            "resume_blocks": len(blocks),
        },
        "source_confidence": {
            "cv_payload": 1.0,
            "cinode_profile": 0.95,
            "enrichment_facts": 0.85,
            "web_discovered_links": 0.7,
        },
    }
