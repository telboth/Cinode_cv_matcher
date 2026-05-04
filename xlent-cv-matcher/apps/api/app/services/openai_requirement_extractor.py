from __future__ import annotations

from typing import Any

from app.models.requirement import Requirement
from app.services.json_utils import parse_json_from_text


REQUIREMENTS_PROMPT = """You extract requirements from consulting opportunities.
Return strict JSON only as an array of objects.
Each object must have: category (must|should|nice_to_have), text (string), weight (0..1).
Do not include any additional keys.
"""

TOOLS_PROMPT = """Extract tools/technologies/methods/languages explicitly requested in this opportunity text.
Examples: Python, Azure, AWS, GCP, Claude Code, Codex, FastAPI, SQL, Kubernetes.
Return strict JSON only as an array of strings.
Rules:
- Max 20 items
- Keep each item short (1-4 words)
- No sentences, no explanations
- Deduplicate semantically equivalent terms
"""


def extract_requirements_with_openai(
    client: Any,
    model: str,
    opportunity_id: str,
    text: str,
    opportunity_title: str = "",
) -> list[Requirement] | None:
    try:
        payload_text = f"Tittel:\n{opportunity_title.strip()}\n\nUtlysningstekst:\n{text.strip()}" if opportunity_title else text
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": REQUIREMENTS_PROMPT},
                {"role": "user", "content": payload_text[:14000]},
            ],
        )
        payload = parse_json_from_text(response.output_text)
        if not isinstance(payload, list):
            return None

        requirements: list[Requirement] = []
        seen_texts: set[str] = set()
        for item in payload[:20]:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", "should"))
            if category not in {"must", "should", "nice_to_have"}:
                category = "should"

            text_value = str(item.get("text", "")).strip()
            if not text_value:
                continue
            key = text_value.lower()
            if key in seen_texts:
                continue
            seen_texts.add(key)

            weight_raw = item.get("weight", 0.55)
            try:
                weight = float(weight_raw)
            except (ValueError, TypeError):
                weight = 0.55

            weight = max(0.0, min(weight, 1.0))
            requirements.append(
                Requirement(
                    opportunity_id=opportunity_id,
                    category=category,
                    text=text_value,
                    weight=weight,
                    extracted_by="ai_llm",
                )
            )

        return requirements
    except Exception:
        return None


def extract_requirement_tools_with_openai(
    client: Any,
    model: str,
    text: str,
    opportunity_title: str = "",
) -> list[str] | None:
    try:
        payload_text = f"Title:\n{opportunity_title.strip()}\n\nOpportunity text:\n{text.strip()}" if opportunity_title else text
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": TOOLS_PROMPT},
                {"role": "user", "content": payload_text[:14000]},
            ],
        )
        parsed = parse_json_from_text(response.output_text)
        if not isinstance(parsed, list):
            return None

        out: list[str] = []
        seen: set[str] = set()
        for item in parsed[:30]:
            value = str(item or "").strip()
            if not value:
                continue
            if len(value) > 60:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(value)
            if len(out) >= 20:
                break
        return out
    except Exception:
        return None
