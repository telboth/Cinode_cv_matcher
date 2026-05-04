import json
from typing import Any


def parse_json_from_text(text: str) -> Any:
    text = text.strip()
    if not text:
        return None

    if text.startswith("```"):
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{") or candidate.startswith("["):
                return json.loads(candidate)

    if text.startswith("{") or text.startswith("["):
        return json.loads(text)

    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [x for x in [start_obj, start_arr] if x != -1]
    if not starts:
        return None

    start = min(starts)
    candidate = text[start:]
    return json.loads(candidate)
