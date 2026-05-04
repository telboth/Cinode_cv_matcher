from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx


def enrich_consultant_data(
    full_name: str,
    profile_payload: dict[str, Any] | None,
    resume_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    sources: list[str] = []
    external_findings: list[dict[str, Any]] = []

    all_urls = _collect_urls_from_payload(profile_payload) + _collect_urls_from_payload(resume_payload)
    linkedin_url = _pick_linkedin_url(all_urls)
    scholar_url = _pick_scholar_url(all_urls)
    github_url = _pick_github_url(all_urls)
    orcid_url = _pick_orcid_url(all_urls)
    researchgate_url = _pick_researchgate_url(all_urls)

    if not linkedin_url:
        linkedin_url = _search_profile_url(full_name, "linkedin")
        if linkedin_url:
            sources.append("web-search:linkedin")
        else:
            warnings.append("Could not resolve LinkedIn URL automatically")
    else:
        sources.append("cv-data:linkedin")

    if not scholar_url:
        scholar_url = _search_profile_url(full_name, "scholar")
        if scholar_url:
            sources.append("web-search:scholar")
        else:
            warnings.append("Could not resolve Google Scholar URL automatically")
    else:
        sources.append("cv-data:scholar")

    publications: list[str] = []
    if scholar_url:
        publications = _fetch_scholar_publications(scholar_url)
        if publications:
            sources.append("scholar:publications")
        else:
            warnings.append("Scholar profile found, but could not extract publication list")

    linkedin_profile_text: str | None = None
    if linkedin_url:
        linkedin_profile_text = _fetch_linkedin_profile_text(linkedin_url)
        if linkedin_profile_text:
            sources.append("linkedin:profile_text")
        else:
            warnings.append("LinkedIn URL found, but could not extract public profile text")

    if not github_url:
        github_url = _search_profile_url(full_name, "github")
        if github_url:
            sources.append("web-search:github")
    else:
        sources.append("cv-data:github")

    if not orcid_url:
        orcid_url = _search_profile_url(full_name, "orcid")
        if orcid_url:
            sources.append("web-search:orcid")
    else:
        sources.append("cv-data:orcid")

    if not researchgate_url:
        researchgate_url = _search_profile_url(full_name, "researchgate")
        if researchgate_url:
            sources.append("web-search:researchgate")
    else:
        sources.append("cv-data:researchgate")

    github_facts = _fetch_github_facts(github_url)
    if github_facts:
        sources.append("github:profile")
        external_findings.append(
            {
                "source_type": "github",
                "url": github_url,
                "confidence": 0.85,
                "facts": github_facts[:8],
            }
        )
    elif github_url:
        warnings.append("GitHub URL found, but could not extract profile facts")

    orcid_facts = _fetch_orcid_facts(orcid_url)
    if orcid_facts:
        sources.append("orcid:profile")
        external_findings.append(
            {
                "source_type": "orcid",
                "url": orcid_url,
                "confidence": 0.8,
                "facts": orcid_facts[:8],
            }
        )
    elif orcid_url:
        warnings.append("ORCID URL found, but could not extract profile facts")

    researchgate_facts = _fetch_researchgate_facts(researchgate_url)
    if researchgate_facts:
        sources.append("researchgate:profile")
        external_findings.append(
            {
                "source_type": "researchgate",
                "url": researchgate_url,
                "confidence": 0.65,
                "facts": researchgate_facts[:6],
            }
        )
    elif researchgate_url:
        warnings.append("ResearchGate URL found, but could not extract profile facts")

    external_fact_lines: list[str] = []
    for finding in external_findings:
        if not isinstance(finding, dict):
            continue
        source_type = str(finding.get("source_type") or "").strip()
        facts = finding.get("facts")
        if not source_type or not isinstance(facts, list):
            continue
        for fact in facts[:5]:
            text = str(fact or "").strip()
            if text:
                external_fact_lines.append(f"{source_type.upper()}: {text}")

    facts = _build_candidate_facts(
        full_name,
        profile_payload,
        resume_payload,
        publications,
        linkedin_url,
        scholar_url,
        linkedin_profile_text,
        external_fact_lines,
    )

    return {
        "linkedin_url": linkedin_url,
        "linkedin_profile_text": linkedin_profile_text,
        "github_url": github_url,
        "orcid_url": orcid_url,
        "researchgate_url": researchgate_url,
        "scholar_url": scholar_url,
        "scholar_publications": publications[:15],
        "candidate_facts": facts,
        "external_findings": external_findings,
        "sources": list(dict.fromkeys(sources)),
        "warnings": warnings,
    }


def _collect_urls_from_payload(payload: Any) -> list[str]:
    found: list[str] = []
    if payload is None:
        return found

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)
        elif isinstance(node, str):
            value = node.strip()
            if value.startswith("http://") or value.startswith("https://"):
                found.append(value)

    walk(payload)
    return list(dict.fromkeys(found))


def _pick_linkedin_url(urls: list[str]) -> str | None:
    for url in urls:
        if "linkedin.com/in/" in url.lower():
            return _strip_tracking(url)
    for url in urls:
        if "linkedin.com" in url.lower():
            return _strip_tracking(url)
    return None


def _pick_scholar_url(urls: list[str]) -> str | None:
    for url in urls:
        lower = url.lower()
        if "scholar.google.com/citations" in lower:
            return _strip_tracking(url)
    for url in urls:
        if "scholar.google.com" in url.lower():
            return _strip_tracking(url)
    return None


def _pick_github_url(urls: list[str]) -> str | None:
    for url in urls:
        lower = url.lower()
        if "github.com/" not in lower:
            continue
        if "/issues" in lower or "/pull" in lower or "/orgs/" in lower:
            continue
        return _strip_tracking(url)
    return None


def _pick_orcid_url(urls: list[str]) -> str | None:
    for url in urls:
        if "orcid.org/" in url.lower():
            return _strip_tracking(url)
    return None


def _pick_researchgate_url(urls: list[str]) -> str | None:
    for url in urls:
        if "researchgate.net/profile/" in url.lower():
            return _strip_tracking(url)
    for url in urls:
        if "researchgate.net" in url.lower():
            return _strip_tracking(url)
    return None


def _strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    if "scholar.google.com" in parsed.netloc:
        query = parse_qs(parsed.query)
        keep = []
        if "user" in query:
            keep.append(f"user={quote_plus(query['user'][0])}")
        if "hl" in query:
            keep.append(f"hl={quote_plus(query['hl'][0])}")
        suffix = f"?{'&'.join(keep)}" if keep else ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{suffix}"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")


def _search_profile_url(full_name: str, kind: str) -> str | None:
    if not full_name.strip():
        return None
    if kind == "linkedin":
        query = f"{full_name} XLENT LinkedIn"
        must_contain = "linkedin.com/in/"
    elif kind == "github":
        query = f"{full_name} GitHub"
        must_contain = "github.com/"
    elif kind == "orcid":
        query = f"{full_name} ORCID"
        must_contain = "orcid.org/"
    elif kind == "researchgate":
        query = f"{full_name} ResearchGate"
        must_contain = "researchgate.net/"
    else:
        query = f"{full_name} Google Scholar"
        must_contain = "scholar.google.com/citations"

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0",
    }
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            return None
        html = resp.text
    except Exception:
        return None

    candidates: list[str] = []
    # DuckDuckGo result links.
    for match in re.findall(r'href="([^"]+)"', html):
        link = unescape(match)
        if "/l/?" in link and "uddg=" in link:
            parsed = urlparse(link)
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg", [])
            if uddg:
                link = unquote(uddg[0])
        if must_contain in link.lower():
            candidates.append(link)

    if not candidates:
        return None
    return _strip_tracking(candidates[0])


def _fetch_scholar_publications(scholar_url: str) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(scholar_url, headers=headers)
        if resp.status_code >= 400:
            return []
        html = resp.text
    except Exception:
        return []

    titles = re.findall(r'class="gsc_a_at"[^>]*>([^<]+)</a>', html)
    cleaned = [unescape(title).strip() for title in titles if title.strip()]
    return list(dict.fromkeys(cleaned))


def _fetch_linkedin_profile_text(linkedin_url: str) -> str | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(linkedin_url, headers=headers)
        if resp.status_code >= 400:
            return None
        final_url = str(resp.url).lower()
        if "/authwall" in final_url or "/checkpoint/" in final_url:
            return None
        html = resp.text
    except Exception:
        return None

    candidates: list[str] = []
    for key in ["og:title", "og:description"]:
        value = _extract_meta_by_property(html, key)
        if value:
            candidates.append(value)
    for key in ["description", "twitter:title", "twitter:description"]:
        value = _extract_meta_by_name(html, key)
        if value:
            candidates.append(value)
    title = _extract_html_title(html)
    if title:
        candidates.append(title)

    cleaned: list[str] = []
    for text in candidates:
        value = _clean_profile_text_line(text)
        if not value:
            continue
        if value in cleaned:
            continue
        cleaned.append(value)

    if not cleaned:
        return None
    merged = " | ".join(cleaned[:4])
    merged = re.sub(r"\s+", " ", merged).strip()
    if len(merged) > 800:
        merged = merged[:797] + "..."
    return merged if len(merged) >= 24 else None


def _extract_meta_by_property(html: str, property_name: str) -> str | None:
    pattern_1 = re.compile(
        rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']',
        flags=re.IGNORECASE,
    )
    match = pattern_1.search(html)
    if match:
        return unescape(match.group(1)).strip()
    pattern_2 = re.compile(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
        flags=re.IGNORECASE,
    )
    match = pattern_2.search(html)
    if match:
        return unescape(match.group(1)).strip()
    return None


def _extract_meta_by_name(html: str, name: str) -> str | None:
    pattern_1 = re.compile(
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        flags=re.IGNORECASE,
    )
    match = pattern_1.search(html)
    if match:
        return unescape(match.group(1)).strip()
    pattern_2 = re.compile(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
        flags=re.IGNORECASE,
    )
    match = pattern_2.search(html)
    if match:
        return unescape(match.group(1)).strip()
    return None


def _extract_html_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, flags=re.IGNORECASE)
    if not match:
        return None
    return unescape(match.group(1)).strip()


def _clean_profile_text_line(text: str) -> str:
    value = re.sub(r"\s+", " ", unescape(text or "")).strip()
    if not value:
        return ""
    blocked_fragments = [
        "join linkedin",
        "sign in",
        "login",
        "linkedin: log in or sign up",
        "linkedin",
    ]
    lowered = value.lower()
    if lowered in blocked_fragments:
        return ""
    if "linkedin" in lowered and len(value) < 25:
        return ""
    return value[:300]


def _github_username_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if "github.com" not in parsed.netloc.lower():
        return None
    parts = [p for p in parsed.path.split("/") if p.strip()]
    if not parts:
        return None
    username = parts[0].strip()
    if username.lower() in {"orgs", "organizations", "topics", "features", "about", "contact"}:
        return None
    return username


def _fetch_github_facts(github_url: str | None) -> list[str]:
    username = _github_username_from_url(github_url)
    if not username:
        return []
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}
    facts: list[str] = []
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            user_resp = client.get(f"https://api.github.com/users/{username}", headers=headers)
            if user_resp.status_code >= 400:
                return []
            user_data = user_resp.json()

            name = str(user_data.get("name") or "").strip()
            bio = str(user_data.get("bio") or "").strip()
            location = str(user_data.get("location") or "").strip()
            public_repos = user_data.get("public_repos")
            followers = user_data.get("followers")

            if name:
                facts.append(f"GitHub navn: {name}")
            if bio:
                facts.append(f"GitHub bio: {bio}")
            if location:
                facts.append(f"GitHub lokasjon: {location}")
            if isinstance(public_repos, int):
                facts.append(f"GitHub public repos: {public_repos}")
            if isinstance(followers, int):
                facts.append(f"GitHub followers: {followers}")

            repos_resp = client.get(
                f"https://api.github.com/users/{username}/repos?sort=updated&per_page=8",
                headers=headers,
            )
            if repos_resp.status_code < 400:
                repos = repos_resp.json()
                if isinstance(repos, list):
                    repo_names: list[str] = []
                    languages: list[str] = []
                    for repo in repos:
                        if not isinstance(repo, dict):
                            continue
                        repo_name = str(repo.get("name") or "").strip()
                        if repo_name:
                            repo_names.append(repo_name)
                        lang = str(repo.get("language") or "").strip()
                        if lang:
                            languages.append(lang)
                    if repo_names:
                        facts.append("Nylig oppdaterte repos: " + ", ".join(repo_names[:5]))
                    if languages:
                        uniq_lang = list(dict.fromkeys(languages))
                        facts.append("Dominerende språk: " + ", ".join(uniq_lang[:5]))
    except Exception:
        return []
    return facts


def _orcid_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if "orcid.org" not in parsed.netloc.lower():
        return None
    parts = [p for p in parsed.path.split("/") if p.strip()]
    if not parts:
        return None
    candidate = parts[-1]
    if re.match(r"^\d{4}-\d{4}-\d{4}-[\dX]{4}$", candidate):
        return candidate
    return None


def _fetch_orcid_facts(orcid_url: str | None) -> list[str]:
    orcid_id = _orcid_id_from_url(orcid_url)
    if not orcid_id:
        return []
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    facts: list[str] = []
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            person_resp = client.get(f"https://pub.orcid.org/v3.0/{orcid_id}/person", headers=headers)
            if person_resp.status_code < 400:
                payload = person_resp.json()
                name_data = payload.get("name") if isinstance(payload, dict) else None
                if isinstance(name_data, dict):
                    given = (
                        name_data.get("given-names", {}).get("value")
                        if isinstance(name_data.get("given-names"), dict)
                        else None
                    )
                    family = (
                        name_data.get("family-name", {}).get("value")
                        if isinstance(name_data.get("family-name"), dict)
                        else None
                    )
                    full_name = " ".join(part for part in [str(given or "").strip(), str(family or "").strip()] if part).strip()
                    if full_name:
                        facts.append(f"ORCID navn: {full_name}")

            works_resp = client.get(f"https://pub.orcid.org/v3.0/{orcid_id}/works", headers=headers)
            if works_resp.status_code < 400:
                works_payload = works_resp.json()
                groups = works_payload.get("group") if isinstance(works_payload, dict) else None
                if isinstance(groups, list):
                    facts.append(f"ORCID registrerte verk: {len(groups)}")
    except Exception:
        return []
    return facts


def _fetch_researchgate_facts(researchgate_url: str | None) -> list[str]:
    if not researchgate_url:
        return []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(researchgate_url, headers=headers)
        if resp.status_code >= 400:
            return []
        html = resp.text
    except Exception:
        return []

    facts: list[str] = []
    title = _extract_meta_by_property(html, "og:title") or _extract_html_title(html)
    description = _extract_meta_by_property(html, "og:description") or _extract_meta_by_name(html, "description")
    if title:
        cleaned = _clean_profile_text_line(title)
        if cleaned:
            facts.append(f"ResearchGate tittel: {cleaned}")
    if description:
        cleaned = _clean_profile_text_line(description)
        if cleaned:
            facts.append(f"ResearchGate beskrivelse: {cleaned}")
    return facts


def _build_candidate_facts(
    full_name: str,
    profile_payload: dict[str, Any] | None,
    resume_payload: dict[str, Any] | None,
    publications: list[str],
    linkedin_url: str | None,
    scholar_url: str | None,
    linkedin_profile_text: str | None,
    external_fact_lines: list[str],
) -> list[str]:
    facts: list[str] = []
    if full_name:
        facts.append(f"Name: {full_name}")

    if isinstance(profile_payload, dict):
        title = profile_payload.get("title")
        location = profile_payload.get("locationName")
        email = profile_payload.get("companyUserEmail")
        if isinstance(title, str) and title.strip():
            facts.append(f"Current title: {title.strip()}")
        if isinstance(location, str) and location.strip():
            facts.append(f"Location: {location.strip()}")
        if isinstance(email, str) and email.strip():
            facts.append(f"Work email: {email.strip()}")

    skill_names: list[str] = []
    if isinstance(resume_payload, dict):
        resume = resume_payload.get("resume")
        if isinstance(resume, dict):
            blocks = resume.get("blocks")
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("blockType") != 12:
                        continue
                    data = block.get("data")
                    if not isinstance(data, list):
                        continue
                    for category in data:
                        if not isinstance(category, dict):
                            continue
                        skills = category.get("skills")
                        if not isinstance(skills, list):
                            continue
                        for item in skills:
                            if not isinstance(item, dict):
                                continue
                            name = item.get("name")
                            if isinstance(name, str) and name.strip():
                                skill_names.append(name.strip())

    if skill_names:
        top_skills = list(dict.fromkeys(skill_names))[:20]
        facts.append(f"Top skills: {', '.join(top_skills)}")

    if linkedin_url:
        facts.append(f"LinkedIn: {linkedin_url}")
    if linkedin_profile_text:
        facts.append(f"LinkedIn profiltekst: {linkedin_profile_text}")
    if scholar_url:
        facts.append(f"Google Scholar: {scholar_url}")
    if publications:
        facts.append(f"Selected publications ({min(len(publications), 5)}): " + "; ".join(publications[:5]))
    for line in external_fact_lines[:20]:
        if line:
            facts.append(line)

    return facts
