from __future__ import annotations

from docx import Document


def read_docx_to_payload(file_path: str) -> dict:
    document = Document(file_path)
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]

    summary = ""
    experience: list[dict[str, str]] = []
    skills: list[str] = []

    if paragraphs:
        summary = paragraphs[0]

    for line in paragraphs[1:]:
        if "," in line and len(skills) < 12 and len(line) < 120:
            maybe_skills = [part.strip() for part in line.split(",") if part.strip()]
            if 1 < len(maybe_skills) <= 10:
                skills.extend(maybe_skills)
                continue

        if len(experience) < 6:
            experience.append(
                {
                    "company": "",
                    "role": "",
                    "period": "",
                    "description": line,
                }
            )

    return {
        "summary": summary,
        "skills": list(dict.fromkeys(skills)),
        "experience": experience,
        "raw_paragraphs": paragraphs,
    }
