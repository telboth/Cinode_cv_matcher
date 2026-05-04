from __future__ import annotations

import json
import re
from typing import Any

from app.models.cv_suggestion import CvSuggestion
from app.models.requirement import Requirement
from app.services.candidate_context_builder import build_candidate_context, build_cv_sections
from app.services.json_utils import parse_json_from_text
from app.services.opportunity_context_builder import build_opportunity_context

SUGGEST_PROMPT = """Du er en senior tilbudsrådgiver som tilpasser CV-tekst til konsulentoppdrag.

Mål:
- Tilpass CV-teksten slik at den matcher kravene i utlysningen.
- Behold teksten sannferdig og dokumenterbar.
- Bevar mest mulig av original ordlyd og tone.

Regler:
- Behold originalt innhold der det er relevant; ikke skriv alt på nytt.
- Du kan korte ned eksisterende tekst med opptil ca. 20 % når det gir bedre klarhet og fokus.
- Legg til kravrelevant tekst naturlig der den passer best.
- Ikke finn opp arbeidsgivere, prosjekter, roller, sertifiseringer, datoer, publikasjoner eller ferdigheter.
- Hvis berikelsesdata finnes (LinkedIn/Scholar/kandidatfakta), bruk det kun som støtte for det som faktisk kan dokumenteres.
- Hold språket konsist og profesjonelt, og skriv på norsk med mindre input tydelig tilsier et annet språk.
- Vurder all tilgjengelig kandidatdata:
  eksisterende CV-payload, strukturerte CV-seksjoner, LinkedIn/Scholar-berikelse og kandidatfakta.
- Vurder utlysningstittel + utlysningstekst + normalisert kravliste.
- Foreslå oppdatert tekst for relevante CV-seksjoner.
- Krav som ser ut som teknologier, verktøy, metoder eller språk (f.eks. Python, Azure, Claude Code, Codex, AWS)
  skal legges til i kompetansefeltet som tilsvarer «Teknikker/Verktøy» (skills), når de er relevante og dokumenterbare.

Outputformat:
- Returner KUN gyldig JSON.
- Toppnivå skal være en array.
- Hvert element må inneholde:
  section_type, original_text, suggested_text, rationale, evidence_json
- evidence_json bør referere til kravtekst og eventuelle støttende profil-/berikelsesfakta som er brukt.
"""


def build_suggestion_prompt_payload(
    profile_payload: dict[str, Any],
    requirements: list[Requirement],
    opportunity_title: str,
    opportunity_text: str,
) -> dict[str, Any]:
    candidate_context = build_candidate_context(profile_payload)
    cv_sections = build_cv_sections(profile_payload, max_change_percent_default=20)
    opportunity_context = build_opportunity_context(opportunity_title, opportunity_text, requirements)

    return {
        "task": "Tailor CV text to opportunity requirements while preserving core original wording.",
        "constraints": {
            "preserve_original_text": True,
            "max_reduction_percent": 20,
            "no_hallucinations": True,
            "language": "Norwegian unless source clearly non-Norwegian",
        },
        "candidate_context": candidate_context,
        "opportunity_context": opportunity_context,
        "cv_sections": cv_sections,
    }


_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _sanitize_value(value: Any, key: str | None = None) -> Any:
    lowered_key = (key or "").lower()

    if isinstance(value, dict):
        return {k: _sanitize_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, key) for item in value]
    if isinstance(value, str):
        if "email" in lowered_key:
            return "[REDACTED_EMAIL]"
        redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
        if len(redacted) > 1200:
            return redacted[:1197] + "..."
        return redacted
    return value


def sanitize_suggestion_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_value(payload)
    if not isinstance(sanitized, dict):
        return {"payload": sanitized}
    return sanitized


def _truncate(text: str, limit: int = 1000) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _normalize_compare_text(text: str) -> str:
    value = (text or "").lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9æøå\s|]", "", value)
    return value.strip()


def _is_near_duplicate_text(a: str, b: str) -> bool:
    a_norm = _normalize_compare_text(a)
    b_norm = _normalize_compare_text(b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True
    shorter, longer = (a_norm, b_norm) if len(a_norm) <= len(b_norm) else (b_norm, a_norm)
    if len(shorter) >= 120 and shorter in longer:
        overlap = len(shorter) / max(len(longer), 1)
        if overlap >= 0.75:
            return True
    return False


def _dedupe_suggestions(items: list[CvSuggestion]) -> list[CvSuggestion]:
    unique: list[CvSuggestion] = []
    for item in items:
        duplicate = False
        for existing in unique:
            same_original = _is_near_duplicate_text(item.original_text, existing.original_text)
            same_suggested = _is_near_duplicate_text(item.suggested_text, existing.suggested_text)
            if same_original or same_suggested:
                duplicate = True
                break
        if not duplicate:
            unique.append(item)
    return unique


def _split_skill_terms(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,\n;|/]+|\s+og\s+|\s+and\s+", text, flags=re.IGNORECASE)
    out: list[str] = []
    for raw in parts:
        value = raw.strip()
        if not value:
            continue
        if len(value) > 80:
            continue
        out.append(value)
    return out


def _normalize_skill_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = cleaned.strip(" .:-")
    return cleaned


def _extract_tools_from_requirements(requirements: list[Requirement], max_items: int = 20) -> list[str]:
    seeds: list[str] = []
    low_signal = {
        "må",
        "skal",
        "bør",
        "erfaring",
        "kjennskap",
        "kompetanse",
        "kunnskap",
        "ha",
        "med",
        "til",
        "innen",
        "minimum",
        "års",
        "year",
        "years",
        "experience",
        "required",
        "must",
        "should",
        "nice to have",
    }
    pattern = re.compile(
        r"(?:erfaring med|kjennskap til|kompetanse i|kunnskap om|must have|experience with)\s+([A-Za-z0-9+.#\-/ ]{2,80})",
        re.IGNORECASE,
    )

    for req in requirements[:25]:
        req_text = str(req.text or "").strip()
        if not req_text:
            continue

        for match in pattern.findall(req_text):
            for candidate in _split_skill_terms(match):
                term = _normalize_skill_name(candidate)
                term_lower = term.lower()
                if not term or term_lower in low_signal:
                    continue
                if len(term.split()) > 4:
                    continue
                if any(verb in term_lower for verb in ["må ha", "bør ha", "erfaring med", "kjennskap til"]):
                    continue
                seeds.append(term)

        for token in _split_skill_terms(req_text):
            token_norm = _normalize_skill_name(token)
            if not token_norm:
                continue
            lower = token_norm.lower()
            if lower in low_signal:
                continue
            if len(token_norm.split()) > 4:
                continue
            if any(verb in lower for verb in ["må ha", "bør ha", "erfaring med", "kjennskap til", "minimum", "års"]):
                continue
            # Keep likely tool/tech tokens.
            if any(ch.isdigit() for ch in token_norm) or any(sym in token_norm for sym in ["+", "#", ".", "-"]) or len(token_norm) <= 24:
                seeds.append(token_norm)

    unique: list[str] = []
    seen: set[str] = set()
    for item in seeds:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= max_items:
            break
    return unique


def _extract_tools_from_text(text: str, max_items: int = 20) -> list[str]:
    if not text:
        return []
    # Reuse requirement parsing by creating lightweight pseudo-requirements from bullet-like lines.
    pseudo_reqs: list[Requirement] = []
    for raw in text.splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        if not any(token in line.lower() for token in ["må", "skal", "bør", "must", "should", "erfaring", "kjennskap"]):
            continue
        pseudo_reqs.append(
            Requirement(
                opportunity_id="",
                category="should",
                text=line,
                weight=0.5,
                extracted_by="text_fallback",
            )
        )
    return _extract_tools_from_requirements(pseudo_reqs, max_items=max_items)


def _build_keyword_suggestions(
    *,
    variant_id: str,
    requirements: list[Requirement],
    base_profile_skills: list[str],
    opportunity_text: str = "",
) -> list[CvSuggestion]:
    tools = _extract_tools_from_requirements(requirements)
    if not tools:
        tools = _extract_tools_from_text(opportunity_text)
    if not tools:
        return []

    profile_skill_keys = {
        _normalize_skill_name(item).lower()
        for item in base_profile_skills
        if _normalize_skill_name(item)
    }
    out: list[CvSuggestion] = []
    seen_keywords: set[str] = set()
    for tool in tools:
        keyword = _normalize_skill_name(tool)
        if not keyword:
            continue
        key = keyword.lower()
        if key in seen_keywords:
            continue
        seen_keywords.add(key)
        if key in profile_skill_keys:
            continue

        source_requirement = ""
        for req in requirements:
            req_text = str(req.text or "").strip()
            if req_text and keyword.lower() in req_text.lower():
                source_requirement = req_text
                break

        out.append(
            CvSuggestion(
                variant_id=variant_id,
                section_type="keyword",
                original_text=source_requirement or f"Kravterm: {keyword}",
                suggested_text=keyword,
                rationale="Foreslått nøkkelord fra krav i utlysningen for Teknikker/Verktøy.",
                evidence_json={"keyword": keyword, "source_requirement": source_requirement},
                status="pending",
            )
        )
    return out[:20]


def heuristic_suggestions(
    variant_id: str,
    profile_payload: dict,
    requirements: list[Requirement],
    opportunity_title: str = "",
    opportunity_text: str = "",
) -> list[CvSuggestion]:
    summary = str(profile_payload.get("summary", "")).strip()
    skills = profile_payload.get("skills") or []
    top_reqs = requirements[:3]

    req_text = "; ".join(req.text for req in top_reqs) if top_reqs else "kundens behov"
    title_text = _truncate(opportunity_title, 120) if opportunity_title else "oppdraget"
    improved_summary = summary or "Konsulent med dokumentert leveranseevne i komplekse prosjekter."
    improved_summary = (
        f"{_truncate(improved_summary, 450)} "
        f"Relevant for {title_text} med fokus på: { _truncate(req_text, 250) }."
    ).strip()

    suggestions = [
        CvSuggestion(
            variant_id=variant_id,
            section_type="summary",
            original_text=summary or "",
            suggested_text=improved_summary,
            rationale="Tilpasset profiltekst mot de høyest vektede kravene.",
            evidence_json={"requirements": [r.text for r in top_reqs]},
            status="pending",
        )
    ]

    if skills:
        selected = [s for s in skills[:8] if isinstance(s, str)]
        skill_text = ", ".join(selected)
        suggestions.append(
            CvSuggestion(
                variant_id=variant_id,
                section_type="skills",
                original_text=", ".join(str(s) for s in skills),
                suggested_text=skill_text,
                rationale="Fokuserer på en kortliste av relevante kompetanser.",
                evidence_json={"count": len(selected)},
                status="pending",
            )
        )

    keyword_suggestions = _build_keyword_suggestions(
        variant_id=variant_id,
        requirements=requirements,
        base_profile_skills=[str(s) for s in skills if isinstance(s, str)],
        opportunity_text=opportunity_text,
    )
    return _dedupe_suggestions([*suggestions, *keyword_suggestions])


def openai_suggestions(
    client: Any,
    model: str,
    variant_id: str,
    profile_payload: dict,
    requirements: list[Requirement],
    opportunity_title: str,
    opportunity_text: str,
    suggestion_prompt_override: str | None = None,
) -> list[CvSuggestion] | None:
    prompt_payload = build_suggestion_prompt_payload(profile_payload, requirements, opportunity_title, opportunity_text)
    section_text_by_type: dict[str, str] = {}
    for section in prompt_payload.get("cv_sections", []):
        if not isinstance(section, dict):
            continue
        section_type = str(section.get("section_type", "")).strip()
        original_text = str(section.get("original_text", "")).strip()
        if section_type and original_text and section_type not in section_text_by_type:
            section_text_by_type[section_type] = original_text

    system_prompt = (suggestion_prompt_override or "").strip() or SUGGEST_PROMPT

    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)[:25000]},
            ],
        )
        parsed = parse_json_from_text(response.output_text)
        if not isinstance(parsed, list):
            return None

        out: list[CvSuggestion] = []
        for item in parsed[:20]:
            if not isinstance(item, dict):
                continue

            section_type = str(item.get("section_type", "summary"))
            original_text = str(item.get("original_text", "")).strip() or section_text_by_type.get(section_type, "")
            suggested_text = str(item.get("suggested_text", "")).strip()
            rationale = str(item.get("rationale", "")).strip() or "AI-generert forslag"
            evidence_json = item.get("evidence_json", {})
            if not suggested_text:
                continue

            if not isinstance(evidence_json, dict):
                evidence_json = {"raw": str(evidence_json)}

            out.append(
                CvSuggestion(
                    variant_id=variant_id,
                    section_type=section_type,
                    original_text=original_text,
                    suggested_text=suggested_text,
                    rationale=rationale,
                    evidence_json=evidence_json,
                    status="pending",
                )
            )

        profile_skills = profile_payload.get("skills") or []
        keyword_suggestions = _build_keyword_suggestions(
            variant_id=variant_id,
            requirements=requirements,
            base_profile_skills=[str(s) for s in profile_skills if isinstance(s, str)],
            opportunity_text=opportunity_text,
        )
        return _dedupe_suggestions([*out, *keyword_suggestions])
    except Exception:
        return None
