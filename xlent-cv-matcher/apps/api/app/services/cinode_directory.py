from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx


class CinodeApiError(Exception):
    pass


_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _parse_datetime_like(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _resume_sort_key(item: dict[str, Any]) -> tuple[float, int]:
    timestamp_fields = [
        "updatedDate",
        "updatedAt",
        "modifiedDate",
        "modifiedAt",
        "lastUpdatedDate",
        "changedDate",
        "createdDate",
        "createdAt",
        "date",
    ]
    best_ts = 0.0
    for field in timestamp_fields:
        dt = _parse_datetime_like(item.get(field))
        if dt is None:
            continue
        ts = dt.timestamp()
        if ts > best_ts:
            best_ts = ts

    try:
        resume_id = int(item.get("id")) if item.get("id") is not None else 0
    except Exception:
        resume_id = 0
    return (best_ts, resume_id)


def _decode_base64_text(value: str) -> str | None:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding).decode("utf-8", errors="ignore")
    except Exception:
        return None
    return decoded


def _is_cinode_basic_credentials_blob(value: str) -> bool:
    decoded = _decode_base64_text(value.strip())
    if not decoded or ":" not in decoded:
        return False
    left, right = decoded.split(":", 1)
    if not left or not right:
        return False
    return left.endswith(".app.cinode.com")


def normalize_auth_value(raw_value: str) -> str:
    value = raw_value.strip()
    lowered = value.lower()
    if lowered.startswith("bearer ") or lowered.startswith("basic "):
        return value
    if _is_cinode_basic_credentials_blob(value):
        return f"Basic {value}"
    return f"Bearer {value}"


def mask_auth_value(auth_value: str) -> str:
    value = auth_value.strip()
    if len(value) <= 12:
        return "***"
    return f"{value[:7]}...{value[-4:]}"


def _decode_company_id_from_jwt(auth_value: str) -> str | None:
    if not auth_value.lower().startswith("bearer "):
        return None

    token = auth_value.split(" ", 1)[1]
    parts = token.split(".")
    if len(parts) < 2:
        return None

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded)
    except Exception:
        return None

    possible_keys = [
        "companyId",
        "company_id",
        "cinodeCompanyId",
        "http://schemas.cinode.com/companyId",
    ]
    for key in possible_keys:
        if key in data and data[key]:
            return str(data[key])

    return None


def _extract_company_user_id_from_whoami(whoami: dict[str, Any] | None) -> str | None:
    if not isinstance(whoami, dict):
        return None
    for key in ["companyUserId", "CompanyUserId", "userId", "UserId", "id", "Id"]:
        value = whoami.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _has_manager_or_teamleader_access(profile: dict[str, Any] | None) -> bool:
    if not isinstance(profile, dict):
        return False

    roles = profile.get("roles")
    if isinstance(roles, list):
        for role in roles:
            if not isinstance(role, dict):
                continue
            level = role.get("level")
            try:
                if level is not None and int(level) >= 300:
                    return True
            except Exception:
                pass
            name = str(role.get("name") or "").strip().lower()
            description = str(role.get("description") or "").strip().lower()
            hay = f"{name} {description}"
            if any(token in hay for token in ["manager", "admin", "teamleader", "team leader", "team manager", "teamleder"]):
                return True

    team_managers = profile.get("teamManagers")
    if isinstance(team_managers, list) and len(team_managers) > 0:
        return True

    return False


def _profile_to_consultant(profile: dict[str, Any], source: str) -> dict[str, Any]:
    external_id = profile.get("id") or profile.get("companyUserId")
    full_name = f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip()
    if not full_name:
        full_name = str(external_id or "Consultant")
    location = profile.get("locationName")
    if not location and isinstance(profile.get("companyAddress"), dict):
        location = profile.get("companyAddress", {}).get("city")
    return {
        "external_id": str(external_id) if external_id is not None else None,
        "full_name": full_name,
        "email": profile.get("companyUserEmail"),
        "location": str(location) if location else None,
        "source": source,
    }


def _request_json(base_url: str, auth_value: str, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": auth_value,
        "Content-Type": "application/json",
    }

    attempts = 0
    while True:
        attempts += 1
        with httpx.Client(timeout=20) as client:
            if method == "POST":
                response = client.post(url, headers=headers, json=body or {})
            else:
                response = client.get(url, headers=headers)

        if response.status_code == 429 and attempts < 4:
            wait_seconds = _parse_retry_after_seconds(response, response.text or "")
            time.sleep(min(max(wait_seconds, 0.2), 3.0))
            continue
        break

    data: Any = None
    try:
        data = response.json()
    except Exception:
        data = response.text

    return response.status_code, data


def _parse_retry_after_seconds(response: httpx.Response, text: str) -> float:
    header_value = response.headers.get("Retry-After")
    if header_value:
        try:
            return max(0.0, float(header_value))
        except Exception:
            pass

    match = re.search(r"after\s+(\d+)\s+seconds?", text or "", flags=re.IGNORECASE)
    if match:
        try:
            return max(0.0, float(match.group(1)))
        except Exception:
            pass

    return 1.0


def _exchange_basic_for_bearer(base_url: str, basic_auth: str) -> str:
    cache_key = f"{base_url.rstrip('/')}|{basic_auth}"
    now = time.time()
    cached = _TOKEN_CACHE.get(cache_key)
    if cached:
        cached_token, cached_exp = cached
        if now < cached_exp - 5:
            return cached_token

    token_url = f"{base_url.rstrip('/')}/token"
    headers = {"Authorization": basic_auth}

    attempts = 0
    while True:
        attempts += 1
        with httpx.Client(timeout=20) as client:
            response = client.get(token_url, headers=headers)

        if response.status_code == 429 and attempts < 4:
            wait_seconds = _parse_retry_after_seconds(response, response.text or "")
            time.sleep(min(max(wait_seconds, 0.2), 3.0))
            continue

        if response.status_code >= 400:
            snippet = response.text[:300] if response.text else ""
            raise CinodeApiError(f"Token exchange failed ({response.status_code}): {snippet}")

        token_payload: Any
        try:
            token_payload = response.json()
        except Exception:
            token_payload = response.text

        access_token: str | None = None
        if isinstance(token_payload, dict):
            for key in ["token", "accessToken", "access_token"]:
                value = token_payload.get(key)
                if isinstance(value, str) and value.strip():
                    access_token = value.strip()
                    break
        elif isinstance(token_payload, str) and token_payload.strip():
            access_token = token_payload.strip().strip('"')

        if not access_token:
            raise CinodeApiError("Token exchange succeeded but no access token was returned")

        # Cinode tokens are short lived. Cache for a conservative 45 seconds.
        _TOKEN_CACHE[cache_key] = (access_token, time.time() + 45.0)
        return access_token


def _resolve_api_authorization(base_url: str, auth_value: str) -> tuple[str, str | None]:
    normalized = normalize_auth_value(auth_value)
    if normalized.lower().startswith("bearer "):
        # Backward compatibility:
        # Earlier UI could store Base64 accessId:accessSecret as Bearer.
        bearer_token = normalized.split(" ", 1)[1].strip()
        if bearer_token and _is_cinode_basic_credentials_blob(bearer_token):
            normalized = f"Basic {bearer_token}"
        else:
            return normalized, None

    # Personal API account credentials in Cinode use Basic auth against /token
    # and then Bearer for subsequent API calls.
    if normalized.lower().startswith("basic "):
        access_token = _exchange_basic_for_bearer(base_url, normalized)
        return f"Bearer {access_token}", None

    return normalized, None


def test_cinode_credential(base_url: str, auth_value: str) -> tuple[bool, int | None, str, dict | None]:
    try:
        api_auth, _ = _resolve_api_authorization(base_url, auth_value)
    except Exception as exc:
        return False, None, f"Credential bootstrap failed: {exc}", None

    whoami_paths = ["/_whoami", "/api/v1/_whoami", "/v0.1/_whoami"]
    first_error: str | None = None
    for path in whoami_paths:
        try:
            status, data = _request_json(base_url, api_auth, path, method="GET")
        except Exception as exc:
            first_error = str(exc)
            continue

        if 200 <= status < 300:
            whoami = data if isinstance(data, dict) else None
            return True, status, "Credential test passed", whoami

        message = data if isinstance(data, str) else str(data)
        if status not in {404, 405}:
            return False, status, f"Credential test failed: {message[:300]}", None
        first_error = message

    # Could not use a known whoami endpoint, but auth may still be usable.
    return False, None, f"Credential test failed: whoami endpoint unavailable ({first_error or 'unknown'})", None


def fetch_cinode_consultants(
    base_url: str,
    auth_value: str,
    oslo_only: bool,
    limit: int,
    path_override: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, str | None, bool, str | None, str | None, str | None]:
    try:
        api_auth, _ = _resolve_api_authorization(base_url, auth_value)
    except Exception as exc:
        raise CinodeApiError(f"Credential bootstrap failed: {exc}") from exc

    whoami_ok, _, _, whoami = test_cinode_credential(base_url, auth_value)
    current_user_id = _extract_company_user_id_from_whoami(whoami if isinstance(whoami, dict) else None)

    company_id: str | None = None
    if whoami_ok and isinstance(whoami, dict):
        company_id = (
            str(whoami.get("companyId"))
            if whoami.get("companyId") is not None
            else str(whoami.get("CompanyId"))
            if whoami.get("CompanyId") is not None
            else None
        )
    if not company_id:
        company_id = _decode_company_id_from_jwt(auth_value)

    current_user_profile: dict[str, Any] | None = None
    current_user_name: str | None = None
    restricted_to_self = False
    access_reason: str | None = None
    if company_id and current_user_id:
        profile_path = f"/v0.1/companies/{company_id}/users/{current_user_id}"
        status, profile_data = _request_json(base_url, api_auth, profile_path, method="GET")
        if 200 <= status < 300 and isinstance(profile_data, dict):
            current_user_profile = profile_data
            current_user_name = (
                f"{profile_data.get('firstName', '')} {profile_data.get('lastName', '')}".strip() or current_user_id
            )
            if not _has_manager_or_teamleader_access(profile_data):
                restricted_to_self = True
                access_reason = (
                    "Bruker mangler Manager/Team leader-tilgang. Konsulentlisten er derfor begrenset til innlogget bruker."
                )

    candidate_paths: list[tuple[str, str]] = []
    if path_override:
        candidate_paths.append(("GET", path_override))
        candidate_paths.append(("POST", path_override))
    if company_id:
        candidate_paths.append(("GET", f"/v0.1/companies/{company_id}/users"))
    candidate_paths.extend(
        [
            ("GET", "/api/v1/companyusersextended"),
            ("GET", "/api/v1/companyusers"),
            ("POST", "/api/v1/companyusers/search"),
            ("GET", "/api/v1/users"),
            ("GET", "/api/v1/employees"),
        ]
    )

    first_success_path: str | None = None
    raw_payload: Any = None

    for method, path in candidate_paths:
        try:
            status, data = _request_json(base_url, api_auth, path, method=method)
        except Exception:
            continue

        if 200 <= status < 300:
            first_success_path = f"{method} {path}"
            raw_payload = data
            break

    if first_success_path is None:
        raise CinodeApiError("Could not fetch consultants from known endpoints. Try path_override.")

    people_lists = _find_people_lists(raw_payload)
    if not people_lists:
        raise CinodeApiError("Found no list payload to normalize consultants from endpoint response")

    candidates = max(people_lists, key=len)
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        person = _to_consultant(item, source=first_success_path)
        if person:
            normalized.append(person)

    # Deduplicate by external_id if present, else full_name/email.
    deduped: dict[str, dict[str, Any]] = {}
    for row in normalized:
        key = row["external_id"] or f"{row['full_name']}|{row.get('email') or ''}"
        deduped[key] = row

    consultants = list(deduped.values())

    if restricted_to_self and current_user_id:
        consultants = [row for row in consultants if str(row.get("external_id") or "") == str(current_user_id)]
        if not consultants and isinstance(current_user_profile, dict):
            consultants = [_profile_to_consultant(current_user_profile, source=first_success_path or "self-profile")]
    elif oslo_only:
        consultants = [
            row
            for row in consultants
            if "oslo" in (row.get("location") or "").lower()
            or "oslo" in row["full_name"].lower()
        ]

    consultants = consultants[: max(1, min(limit, 2000))]

    return (
        consultants,
        first_success_path,
        company_id,
        restricted_to_self,
        current_user_id,
        current_user_name,
        access_reason,
    )


def fetch_cinode_consultant_cv(
    base_url: str,
    auth_value: str,
    consultant_id: str,
    resume_id: int | None = None,
) -> dict[str, Any]:
    try:
        api_auth, _ = _resolve_api_authorization(base_url, auth_value)
    except Exception as exc:
        raise CinodeApiError(f"Credential bootstrap failed: {exc}") from exc

    whoami_ok, _, _, whoami = test_cinode_credential(base_url, auth_value)
    company_id: str | None = None
    if whoami_ok and isinstance(whoami, dict):
        company_id = (
            str(whoami.get("companyId"))
            if whoami.get("companyId") is not None
            else str(whoami.get("CompanyId"))
            if whoami.get("CompanyId") is not None
            else None
        )
    if not company_id:
        company_id = _decode_company_id_from_jwt(auth_value)
    if not company_id:
        raise CinodeApiError("Could not resolve company id for consultant CV fetch")

    user_path = f"/v0.1/companies/{company_id}/users/{consultant_id}"
    status, profile = _request_json(base_url, api_auth, user_path, method="GET")
    if status >= 400 or not isinstance(profile, dict):
        message = profile if isinstance(profile, str) else str(profile)
        raise CinodeApiError(f"Could not fetch consultant profile: {status} {message[:300]}")

    resumes = profile.get("resumes")
    if not isinstance(resumes, list):
        resumes = []
    resume_dicts = [item for item in resumes if isinstance(item, dict)]
    if resume_dicts:
        resume_dicts.sort(key=_resume_sort_key, reverse=True)
        resumes = resume_dicts

    selected_resume_id: int | None = resume_id
    if selected_resume_id is None and resumes:
        first_resume = resumes[0]
        if isinstance(first_resume, dict) and first_resume.get("id") is not None:
            try:
                selected_resume_id = int(first_resume["id"])
            except Exception:
                selected_resume_id = None

    resume_payload: dict[str, Any] | None = None
    source_path = user_path
    if selected_resume_id is not None:
        resume_path = f"/v0.1/companies/{company_id}/users/{consultant_id}/resumes/{selected_resume_id}"
        resume_status, resume_data = _request_json(base_url, api_auth, resume_path, method="GET")
        if 200 <= resume_status < 300 and isinstance(resume_data, dict):
            resume_payload = resume_data
            source_path = resume_path

    return {
        "company_id": company_id,
        "source_path": source_path,
        "selected_resume_id": selected_resume_id,
        "profile": profile,
        "resume": resume_payload,
        "resumes": resumes,
        "full_name": f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip() or str(profile.get("id") or consultant_id),
        "email": profile.get("companyUserEmail"),
        "title": profile.get("title"),
        "location": profile.get("locationName")
        or (
            profile.get("companyAddress", {}).get("city")
            if isinstance(profile.get("companyAddress"), dict)
            else None
        ),
    }


def check_public_resume_url(url: str) -> tuple[bool, int | None, str]:
    if not url or not url.strip():
        return False, None, "Missing public resume URL"

    value = url.strip()
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(value)
        status = response.status_code
        final_url = str(response.url).lower()
        body_hint = (response.text[:500] if response.text else "").lower()

        if 200 <= status < 300:
            if "login" in final_url or "signin" in final_url or "logga in" in body_hint:
                return False, status, "Public URL resolves to sign-in page"
            return True, status, "Public URL is reachable"

        if status in {401, 403}:
            return False, status, "Public URL requires authentication"
        if status == 404:
            return False, status, "Public URL not found"
        return False, status, f"Public URL returned status {status}"
    except Exception as exc:
        return False, None, f"Public URL check failed: {exc}"


def _find_people_lists(payload: Any) -> list[list[dict[str, Any]]]:
    lists: list[list[dict[str, Any]]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, list):
            if node and all(isinstance(item, dict) for item in node):
                lists.append(node)
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, depth + 1)

    walk(payload)
    return lists


def _to_consultant(item: dict[str, Any], source: str) -> dict[str, Any] | None:
    external_id = item.get("id") or item.get("Id") or item.get("userId") or item.get("UserId")

    full_name = (
        item.get("fullName")
        or item.get("FullName")
        or item.get("name")
        or item.get("Name")
    )
    if not full_name:
        first = item.get("firstName") or item.get("FirstName") or ""
        last = item.get("lastName") or item.get("LastName") or ""
        full_name = f"{first} {last}".strip()

    email = item.get("email") or item.get("Email")

    location = (
        item.get("city")
        or item.get("City")
        or item.get("location")
        or item.get("Location")
        or item.get("office")
        or item.get("Office")
    )

    if not full_name:
        return None

    return {
        "external_id": str(external_id) if external_id is not None else None,
        "full_name": str(full_name),
        "email": str(email) if email else None,
        "location": str(location) if location else None,
        "source": source,
    }
