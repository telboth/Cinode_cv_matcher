from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from app.services.cinode_directory import _request_json, _resolve_api_authorization


class CinodeBrowserCreateCvError(Exception):
    pass


def _text_fingerprint(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return "empty"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    preview = re.sub(r"\s+", " ", value)[:40]
    return f"len={len(value)} sha1={digest} preview='{preview}'"


def login_cinode_browser_profile(
    *,
    app_url: str,
    company_slug: str = "xlent",
    headless: bool,
    timeout_ms: int,
) -> dict[str, Any]:
    if not app_url.strip():
        raise CinodeBrowserCreateCvError("Missing CINODE_APP_URL")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:
        raise CinodeBrowserCreateCvError(
            "Playwright mangler. Kjør: pip install playwright && python -m playwright install chromium"
        ) from exc

    logs_dir = Path(__file__).resolve().parents[4] / ".run"
    logs_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = logs_dir / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    tenant = _normalize_company_slug(company_slug)
    start_url = f"{app_url.rstrip('/')}/{tenant}/resumes"
    trace: list[str] = [f"Starter login-flyt: {start_url}"]

    async def _run() -> dict[str, Any]:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                ignore_https_errors=True,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                try:
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except PlaywrightTimeoutError as exc:
                    raise CinodeBrowserCreateCvError(f"Timeout ved åpning av Cinode login-side: {start_url}") from exc
                except Exception as exc:
                    message = str(exc)
                    if "interrupted by another navigation" in message.lower():
                        trace.append("Navigering ble omdirigert, fortsetter med aktiv side")
                        await page.wait_for_timeout(1500)
                    else:
                        raise CinodeBrowserCreateCvError(f"Kunne ikke åpne Cinode login-side: {message}") from exc

                # Fast path: if session is already valid, do not keep the window open for a long settle cycle.
                readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 8000), trace=trace)
                if readiness == "login":
                    trace.append("Innloggingsside oppdaget, venter på manuell innlogging")
                    await _wait_for_manual_login(page, headless=headless, timeout_ms=timeout_ms)
                    trace.append("Innlogging fullført")
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await page.wait_for_timeout(800)
                    readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 15000), trace=trace)
                elif readiness == "loading":
                    trace.append("Portal bruker litt tid, gjør en kort ekstra klar-sjekk")
                    readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 10000), trace=trace)

                if readiness != "ready":
                    raise CinodeBrowserCreateCvError(
                        "Cinode-portalen ble ikke klar etter innlogging. "
                        f"Tilstand: {readiness}. URL: {page.url}"
                    )

                current_url = page.url
                await context.close()
                return {
                    "ok": True,
                    "detail": "Innlogging for automasjonsprofil er klar.",
                    "current_url": current_url,
                    "debug_trace": trace,
                }
            except Exception:
                await context.close()
                raise

    try:
        return asyncio.run(_run())
    except CinodeBrowserCreateCvError:
        raise
    except Exception as exc:
        raise CinodeBrowserCreateCvError(str(exc)) from exc


def preflight_cinode_resume_edit_access(
    *,
    app_url: str,
    company_slug: str,
    consultant_id: str,
    resume_id: int,
    resume_slug: str,
    owner_company_user_id: str | int | None,
    headless: bool,
    timeout_ms: int,
) -> dict[str, Any]:
    if not app_url.strip():
        raise CinodeBrowserCreateCvError("Missing CINODE_APP_URL")
    if not resume_id:
        raise CinodeBrowserCreateCvError("Missing resume_id for preflight")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:
        raise CinodeBrowserCreateCvError(
            "Playwright mangler. Kjør: pip install playwright && python -m playwright install chromium"
        ) from exc

    logs_dir = Path(__file__).resolve().parents[4] / ".run"
    logs_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = logs_dir / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    tenant = _normalize_company_slug(company_slug)
    owner_id = str(owner_company_user_id or consultant_id or "").strip()
    clean_slug = str(resume_slug or "").strip().strip("/")
    if not owner_id:
        raise CinodeBrowserCreateCvError("Missing owner_company_user_id for preflight")
    if not clean_slug:
        clean_slug = f"resume-{resume_id}"
    edit_url = f"{app_url.rstrip('/')}/{tenant}/resumes/user/{owner_id}/edit/{resume_id}/{clean_slug}"
    start_url = f"{app_url.rstrip('/')}/{tenant}/resumes"
    trace: list[str] = [
        f"Preflight start: {start_url}",
        f"Preflight edit-URL: {edit_url}",
    ]

    async def _run() -> dict[str, Any]:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                ignore_https_errors=True,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 15000), trace=trace)
                if readiness == "login":
                    trace.append("Preflight: login kreves, venter på manuell innlogging")
                    await _wait_for_manual_login(page, headless=headless, timeout_ms=timeout_ms)
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 15000), trace=trace)
                if readiness != "ready":
                    return {
                        "ok": False,
                        "detail": f"Preflight feilet: Cinode-portalen ble ikke klar (tilstand={readiness}).",
                        "consultant_id": consultant_id,
                        "resume_id": resume_id,
                        "edit_url": edit_url,
                        "current_url": page.url,
                        "debug_trace": trace,
                    }

                await page.goto(edit_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(1200)
                current_url = page.url
                body_text = (await page.locator("body").inner_text()).lower()
                body_text_compact = re.sub(r"\s+", " ", body_text)[:2000]
                if any(
                    marker in body_text_compact
                    for marker in [
                        "403",
                        "don't have enough access",
                        "do not have enough access",
                        "not enough access",
                        "ikke tilgang",
                        "du har ikke tilgang",
                    ]
                ):
                    trace.append("Preflight: tilgang nektet (403/insufficient access)")
                    return {
                        "ok": False,
                        "detail": (
                            "Preflight feilet: innlogget Cinode-bruker mangler tilgang til å redigere valgt konsulent sin CV "
                            "(403). Bruk konto med Manager/Admin-rettigheter i riktig team, eller logg inn som riktig bruker."
                        ),
                        "consultant_id": consultant_id,
                        "resume_id": resume_id,
                        "edit_url": edit_url,
                        "current_url": current_url,
                        "debug_trace": trace,
                    }

                expected_fragment = f"/edit/{resume_id}/"
                if expected_fragment in current_url.lower():
                    trace.append("Preflight OK: riktig edit-side åpnet")
                    return {
                        "ok": True,
                        "detail": "Preflight OK: valgt CV er redigerbar i aktiv Cinode-sesjon.",
                        "consultant_id": consultant_id,
                        "resume_id": resume_id,
                        "edit_url": edit_url,
                        "current_url": current_url,
                        "debug_trace": trace,
                    }

                trace.append("Preflight: ukjent redirect/tilstand ved åpning av edit-side")
                return {
                    "ok": False,
                    "detail": (
                        "Preflight feilet: kunne ikke verifisere tilgang til valgt CV-edit-side i aktiv sesjon. "
                        "Sjekk at du er logget inn i riktig tenant og har tilgang til konsulenten."
                    ),
                    "consultant_id": consultant_id,
                    "resume_id": resume_id,
                    "edit_url": edit_url,
                    "current_url": current_url,
                    "debug_trace": trace,
                }
            except PlaywrightTimeoutError as exc:
                return {
                    "ok": False,
                    "detail": f"Preflight timeout: {exc}",
                    "consultant_id": consultant_id,
                    "resume_id": resume_id,
                    "edit_url": edit_url,
                    "current_url": page.url if not page.is_closed() else None,
                    "debug_trace": trace,
                }
            finally:
                await context.close()

    try:
        return asyncio.run(_run())
    except CinodeBrowserCreateCvError:
        raise
    except Exception as exc:
        raise CinodeBrowserCreateCvError(str(exc)) from exc


def create_cinode_cv_via_browser(
    *,
    app_url: str,
    consultant_id: str,
    consultant_name: str,
    title: str,
    docx_bytes: bytes,
    presentation_text: str | None,
    skills_keywords: list[str] | None,
    resume_seed_url: str | None,
    company_slug: str = "xlent",
    preferred_template_keyword: str = "XLENT",
    clean_new_cv_content: bool = True,
    source_resume_docx_bytes: bytes | None = None,
    require_source_docx_import: bool = True,
    headless: bool,
    timeout_ms: int,
    source_resume_id: int | None = None,
    strict_deterministic_mode: bool = True,
    cinode_api_base_url: str | None = None,
    cinode_api_auth_value: str | None = None,
    cinode_company_id: str | None = None,
) -> dict[str, Any]:
    if not app_url.strip():
        raise CinodeBrowserCreateCvError("Missing CINODE_APP_URL")

    if not docx_bytes:
        raise CinodeBrowserCreateCvError("Missing DOCX payload for browser import")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:
        raise CinodeBrowserCreateCvError(
            "Playwright mangler. Kjør: pip install playwright && python -m playwright install chromium"
        ) from exc

    # Keep the DOCX generation for debugging/auditing, even when UI update is the primary strategy.
    temp_dir = Path(tempfile.mkdtemp(prefix="cinode-cv-import-"))
    source_file = temp_dir / "cv_import.docx"
    source_file.write_bytes(docx_bytes)
    source_resume_file: Path | None = None
    if source_resume_docx_bytes:
        source_resume_file = temp_dir / "source_resume.docx"
        source_resume_file.write_bytes(source_resume_docx_bytes)

    logs_dir = Path(__file__).resolve().parents[4] / ".run"
    logs_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = logs_dir / "cinode-create-cv-failure.png"
    profile_dir = logs_dir / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    app_base = app_url.rstrip("/")
    tenant = _normalize_company_slug(company_slug)
    candidate_urls = _candidate_urls(app_base, resume_seed_url, tenant, consultant_id=consultant_id)
    trace: list[str] = [
        "AUTOMATION_VERSION=2026-05-04-strict-deterministic-v7",
        f"STRICT_DETERMINISTIC_MODE={'on' if strict_deterministic_mode else 'off'}",
        f"Kandidater: {len(candidate_urls)} URL-er",
    ]

    async def _run() -> dict[str, Any]:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                ignore_https_errors=True,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            last_error: str | None = None
            created_cv_started = False

            try:
                for candidate in candidate_urls:
                    if page.is_closed():
                        replacement = _get_latest_open_page(context)
                        if replacement is None:
                            last_error = "Ingen aktiv browser-fane tilgjengelig"
                            trace.append(last_error)
                            break
                        page = replacement
                        trace.append("Byttet til aktiv browser-fane etter lukket side")

                    trace.append(f"Navigerer til: {candidate}")
                    try:
                        await page.goto(candidate, wait_until="domcontentloaded", timeout=timeout_ms)
                    except PlaywrightTimeoutError:
                        last_error = f"Timeout ved lasting av side: {candidate}"
                        trace.append(last_error)
                        continue
                    except Exception as exc:
                        message = str(exc)
                        if "interrupted by another navigation" in message.lower():
                            trace.append(f"Navigering ble omdirigert på {candidate}, fortsetter med aktiv side")
                            await page.wait_for_timeout(1500)
                        else:
                            last_error = f"Navigering feilet på {candidate}: {exc}"
                            trace.append(last_error[:300])
                            continue

                    readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 30000), trace=trace)
                    if readiness == "login":
                        trace.append("Portal i login-tilstand, forsøker manuell innlogging")
                        await _wait_for_manual_login(page, headless=headless, timeout_ms=timeout_ms)
                        await page.goto(candidate, wait_until="domcontentloaded", timeout=timeout_ms)
                        readiness = await _wait_for_portal_state(page, timeout_ms=min(timeout_ms, 30000), trace=trace)
                    if readiness != "ready":
                        last_error = f"Portal ikke klar på {candidate} (tilstand={readiness})"
                        trace.append(last_error)
                        continue

                    if strict_deterministic_mode:
                        try:
                            resumes_url_for_lookup = _resume_list_url_from_current(page.url or "") or candidate
                            ids_before_create: set[int] = set()
                            try:
                                before_links = await _collect_resume_links_from_list(page)
                                ids_before_create = {
                                    int(row.get("id") or 0) for row in before_links if int(row.get("id") or 0) > 0
                                }
                                trace.append(f"CV-er før copy (strict): {len(ids_before_create)}")
                            except Exception:
                                trace.append("Kunne ikke lese CV-liste før copy (strict)")

                            if not source_resume_id:
                                raise CinodeBrowserCreateCvError(
                                    "Strict deterministic mode krever valgt utgangspunkt-CV (resume_id)."
                                )

                            source_visible = await _ensure_source_resume_visible_on_list(
                                page=page,
                                source_resume_id=int(source_resume_id),
                                list_url=resumes_url_for_lookup,
                                consultant_id=consultant_id,
                                consultant_name=consultant_name,
                                timeout_ms=min(timeout_ms, 30000),
                                trace=trace,
                            )
                            if not source_visible:
                                raise CinodeBrowserCreateCvError(
                                    "Strict deterministic mode: valgt utgangspunkt-CV er ikke synlig i listen. "
                                    "Dette skyldes vanligvis tilgang/scope (f.eks. 'Yours' i stedet for 'All') "
                                    "eller manglende tilgang til kollegaens CV."
                                )

                            copied_resume_id = await _try_copy_selected_resume(
                                page=page,
                                source_resume_id=int(source_resume_id),
                                consultant_id=consultant_id,
                                consultant_name=consultant_name,
                                title=title,
                                ids_before_create=ids_before_create,
                                list_url=resumes_url_for_lookup,
                                timeout_ms=min(timeout_ms, 30000),
                                trace=trace,
                            )
                            if not copied_resume_id:
                                raise CinodeBrowserCreateCvError(
                                    "Strict deterministic mode: kunne ikke kopiere valgt utgangspunkt-CV."
                                )
                            created_cv_started = True
                            trace.append(f"Strict mode: kopierte valgt kilde-CV (id={int(source_resume_id)}) -> ny id={int(copied_resume_id)}")

                            current_after_copy = _extract_resume_id_from_url(page.url or "")
                            if not current_after_copy or int(current_after_copy) != int(copied_resume_id):
                                await _open_resume_editor_by_id(
                                    page=page,
                                    list_url=resumes_url_for_lookup,
                                    resume_id=int(copied_resume_id),
                                    timeout_ms=min(timeout_ms, 30000),
                                    trace=trace,
                                )
                            else:
                                trace.append(f"Strict mode: kopi åpnet direkte i editor (id={int(copied_resume_id)})")

                            trace.append("Strict mode: oppdaterer presentasjonstekst i CV-editor")
                            await _update_resume_presentation_text(
                                page=page,
                                presentation_text=presentation_text or "",
                                timeout_ms=timeout_ms,
                                trace=trace,
                                strict_verify=False,
                                expected_title=None,
                            )
                            if (presentation_text or "").strip():
                                trace.append(
                                    f"Presentasjonstekst oppdatert én gang ({_text_fingerprint(presentation_text or '')})"
                                )

                            await _update_resume_skills_keywords(
                                page=page,
                                skills_keywords=skills_keywords or [],
                                timeout_ms=timeout_ms,
                                trace=trace,
                                expected_title=None,
                                strict_mode=True,
                                cinode_api_base_url=cinode_api_base_url,
                                cinode_api_auth_value=cinode_api_auth_value,
                                cinode_company_id=cinode_company_id,
                                consultant_id=consultant_id,
                            )

                            renamed = await _rename_resume_title_from_list_by_id(
                                page=page,
                                list_url=resumes_url_for_lookup,
                                resume_id=int(copied_resume_id),
                                new_title=title,
                                timeout_ms=min(timeout_ms, 20000),
                                trace=trace,
                            )
                            if not renamed:
                                raise CinodeBrowserCreateCvError(
                                    f"Strict deterministic mode: klarte ikke rename ny CV til '{title}'."
                                )
                            await _open_resume_editor_by_id(
                                page=page,
                                list_url=resumes_url_for_lookup,
                                resume_id=int(copied_resume_id),
                                timeout_ms=min(timeout_ms, 20000),
                                trace=trace,
                            )

                            trace.append(f"Midlertidig DOCX lagret for debug: {source_file}")
                            current_url = page.url
                            detail = f"Ny CV opprettet og presentasjonstekst oppdatert for {consultant_name}."
                            await context.close()
                            return {
                                "ok": True,
                                "mode": "browser_automation",
                                "detail": detail,
                                "target_url": candidate,
                                "created_resume_url": current_url,
                                "screenshot_path": None,
                                "debug_trace": trace,
                            }
                        except Exception as exc:
                            last_error = f"Steg feilet på {candidate}: {exc}"
                            trace.append(last_error[:300])
                            if created_cv_started:
                                trace.append(
                                    "CV-oppretting var påbegynt; avbryter videre URL-forsøk for å unngå duplikater"
                                )
                                break
                            trace.append(
                                "Strict mode: ingen CV opprettet ennå, prøver neste kandidat-URL for riktig konsulent-kontekst"
                            )
                            continue

                    try:
                        resumes_url_for_lookup = _resume_list_url_from_current(page.url or "") or candidate
                        ids_before_create: set[int] = set()
                        try:
                            before_links = await _collect_resume_links_from_list(page)
                            ids_before_create = {int(row.get("id") or 0) for row in before_links if int(row.get("id") or 0) > 0}
                            trace.append(f"CV-er før oppretting: {len(ids_before_create)}")
                        except Exception:
                            trace.append("Kunne ikke lese CV-liste før oppretting")

                        copied_from_source = False
                        copied_resume_id: int | None = None
                        imported_source_docx = False
                        editor_title_guard: str | None = title
                        if source_resume_id:
                            copied_resume_id = await _try_copy_selected_resume(
                                page=page,
                                source_resume_id=int(source_resume_id),
                                consultant_id=consultant_id,
                                consultant_name=consultant_name,
                                title=title,
                                ids_before_create=ids_before_create,
                                list_url=resumes_url_for_lookup,
                                timeout_ms=min(timeout_ms, 30000),
                                trace=trace,
                            )
                            copied_from_source = copied_resume_id is not None
                            if copied_from_source:
                                created_cv_started = True
                                trace.append(f"Kopierte valgt kilde-CV (id={int(source_resume_id)})")
                                # For kopiert CV låser vi på resume-id, ikke tittel, for å unngå falske sikkerhetsstopp.
                                editor_title_guard = None
                                current_after_copy = _extract_resume_id_from_url(page.url or "")
                                if copied_resume_id and current_after_copy and int(current_after_copy) == int(copied_resume_id):
                                    trace.append(f"Kopi åpnet allerede i riktig editor (id={copied_resume_id}); hopper over listebasert åpning")
                                else:
                                    await _open_resume_editor_by_id(
                                        page=page,
                                        list_url=resumes_url_for_lookup,
                                        resume_id=int(copied_resume_id) if copied_resume_id else None,
                                        timeout_ms=min(timeout_ms, 30000),
                                        trace=trace,
                                    )

                        if not copied_from_source:
                            trace.append("Prøver å klikke 'opprett CV'")
                            pages_before = len([p for p in context.pages if not p.is_closed()])
                            await _click_create_cv(page=page, timeout_ms=15000)
                            created_cv_started = True
                            await page.wait_for_timeout(1500)
                            open_pages = [p for p in context.pages if not p.is_closed()]
                            if len(open_pages) > pages_before:
                                page = open_pages[-1]
                                trace.append("Ny fane/siden ble åpnet etter 'opprett CV'; bytter til den")
                                await page.bring_to_front()
                                await page.wait_for_timeout(1200)

                            trace.append("Setter språk/template og lagrer ny CV")
                            imported_source_docx = await _configure_and_save_new_cv_dialog(
                                page=page,
                                title=title,
                                import_docx_path=str(source_resume_file) if source_resume_file else None,
                                require_docx_import=require_source_docx_import,
                                disable_generate_ai=True,
                                preferred_language="Norwegian",
                                preferred_template_keyword=preferred_template_keyword,
                                timeout_ms=timeout_ms,
                                trace=trace,
                            )
                            if source_resume_file:
                                trace.append(f"Kilde-CV DOCX brukt i oppretting: {source_resume_file.name}")
                                if not imported_source_docx:
                                    trace.append(
                                        "ADVARSEL: Kilde-CV DOCX ble ikke importert i dialogen. "
                                        "Faller tilbake til template + clean mode + tekstoverstyring."
                                    )
                            if _is_edit_url(page.url):
                                trace.append(f"Ny CV-editor åpnet direkte etter lagring: {page.url}")
                            elif await _is_resume_editor_page(page):
                                trace.append("Ny CV-editor oppdaget direkte etter lagring")
                            else:
                                trace.append("Finder nyopprettet CV i liste og åpner riktig editor")
                                await _open_newly_created_resume_editor(
                                    page=page,
                                    list_url=resumes_url_for_lookup,
                                    expected_title=title,
                                    ids_before_create=ids_before_create,
                                    timeout_ms=min(timeout_ms, 30000),
                                    trace=trace,
                                )

                        if require_source_docx_import and source_resume_file and (not copied_from_source) and (not imported_source_docx):
                            current_resume_id = _extract_resume_id_from_url(page.url or "")
                            # Hard-stop only if we are still on the source CV (risk of editing original),
                            # otherwise continue with explicit warning and apply text override on the new CV.
                            if source_resume_id and current_resume_id and int(current_resume_id) == int(source_resume_id):
                                raise CinodeBrowserCreateCvError(
                                    "Kunne ikke bruke valgt utgangspunkt-CV: verken kopiering av kilde-CV eller DOCX-import lyktes, "
                                    "og aktiv editor matcher original-CV. Avbryter for sikkerhet."
                                )
                            trace.append(
                                "ADVARSEL: Kilde-CV ble ikke kopiert/importert sikkert, men ny CV-editor er åpnet. "
                                "Fortsetter med tekstoppdatering i ny CV."
                            )
                        if clean_new_cv_content:
                            trace.append("Clean mode: skjuler/tømmer prosjektseksjoner i ny CV")
                            await _clean_new_cv_sections(
                                page=page,
                                timeout_ms=min(timeout_ms, 30000),
                                trace=trace,
                                expected_title=editor_title_guard,
                            )

                        trace.append("Oppdaterer presentasjonstekst i CV-editor")
                        await _update_resume_presentation_text(
                            page=page,
                            presentation_text=presentation_text or "",
                            timeout_ms=timeout_ms,
                            trace=trace,
                            strict_verify=False,
                            expected_title=editor_title_guard,
                        )
                        if (presentation_text or "").strip():
                            trace.append(
                                f"Presentasjonstekst oppdatert én gang ({_text_fingerprint(presentation_text or '')})"
                            )
                        await _update_resume_skills_keywords(
                            page=page,
                            skills_keywords=skills_keywords or [],
                            timeout_ms=timeout_ms,
                            trace=trace,
                            expected_title=editor_title_guard,
                            cinode_api_base_url=cinode_api_base_url,
                            cinode_api_auth_value=cinode_api_auth_value,
                            cinode_company_id=cinode_company_id,
                            consultant_id=consultant_id,
                        )

                        # Final, enkel rename-pass etter at innhold er oppdatert.
                        final_resume_id = copied_resume_id or _extract_resume_id_from_url(page.url or "")
                        if final_resume_id:
                            await _rename_resume_title_from_list_by_id(
                                page=page,
                                list_url=resumes_url_for_lookup,
                                resume_id=int(final_resume_id),
                                new_title=title,
                                timeout_ms=min(timeout_ms, 20000),
                                trace=trace,
                            )
                            await _open_resume_editor_by_id(
                                page=page,
                                list_url=resumes_url_for_lookup,
                                resume_id=int(final_resume_id),
                                timeout_ms=min(timeout_ms, 20000),
                                trace=trace,
                            )

                        trace.append(f"Midlertidig DOCX lagret for debug: {source_file}")
                        current_url = page.url
                        detail = f"Ny CV opprettet og presentasjonstekst oppdatert for {consultant_name}."
                        await context.close()
                        return {
                            "ok": True,
                            "mode": "browser_automation",
                            "detail": detail,
                            "target_url": candidate,
                            "created_resume_url": current_url,
                            "screenshot_path": None,
                            "debug_trace": trace,
                        }
                    except Exception as exc:
                        last_error = f"Steg feilet på {candidate}: {exc}"
                        trace.append(last_error[:300])
                        if created_cv_started:
                            trace.append("CV-oppretting var påbegynt; avbryter videre URL-forsøk for å unngå duplikater")
                            break
                        continue

                if not last_error:
                    last_error = "Ukjent feil under UI-automasjon"
                if not page.is_closed():
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                trace.append(f"Skjermbilde lagret: {screenshot_path}")
                debug_blob = " | ".join(trace[-12:])
                if not headless:
                    trace.append("Holder vindu åpent 25 sek etter feil for inspeksjon")
                    if not page.is_closed():
                        await page.wait_for_timeout(25000)
                raise CinodeBrowserCreateCvError(f"{last_error}. Trace: {debug_blob}")
            except CinodeBrowserCreateCvError:
                if not screenshot_path.exists():
                    if not page.is_closed():
                        await page.wait_for_timeout(1200)
                        await page.screenshot(path=str(screenshot_path), full_page=True)
                await context.close()
                raise
            except Exception as exc:
                if not page.is_closed():
                    await page.wait_for_timeout(1200)
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                await context.close()
                raise CinodeBrowserCreateCvError(f"Uventet feil i browser-automasjon: {exc}") from exc

    try:
        result = asyncio.run(_run())
        return result
    except CinodeBrowserCreateCvError:
        raise
    except Exception as exc:
        raise CinodeBrowserCreateCvError(str(exc)) from exc


def _candidate_urls(
    app_base_url: str,
    resume_seed_url: str | None,
    company_slug: str,
    consultant_id: str | None = None,
) -> list[str]:
    urls: list[str] = []
    urls.append(f"{app_base_url}/{company_slug}/resumes")
    if resume_seed_url and resume_seed_url.strip():
        parsed = urlparse(resume_seed_url)
        if parsed.scheme and parsed.netloc:
            resumes_root = f"{parsed.scheme}://{parsed.netloc}/{company_slug}/resumes"
            if resumes_root not in urls:
                urls.append(resumes_root)
    # Do not add legacy fallback routes; they can 404 in newer Cinode tenants.
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def _normalize_company_slug(value: str | None) -> str:
    raw = (value or "xlent").strip().lower()
    if raw in {"xlent", "differ", "folden"}:
        return raw
    return "xlent"


def _extract_resume_id_from_url(value: str) -> int | None:
    text = str(value or "")
    match = re.search(r"/edit/(\d+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


async def _collect_resume_links_from_list(page: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    anchors = page.locator("a[href*='/resumes/user/'][href*='/edit/']")
    try:
        count = await anchors.count()
    except Exception:
        count = 0
    for idx in range(min(count, 400)):
        link = anchors.nth(idx)
        try:
            href = (await link.get_attribute("href")) or ""
            if not href:
                continue
            text = (await link.inner_text()).strip()
            rid = _extract_resume_id_from_url(href)
            if rid is None:
                continue
            items.append({"id": rid, "href": href, "title": text})
        except Exception:
            continue
    dedup: dict[int, dict[str, Any]] = {}
    for row in items:
        dedup[int(row["id"])] = row
    return list(dedup.values())


async def _ensure_source_resume_visible_on_list(
    *,
    page: Any,
    source_resume_id: int,
    list_url: str | None,
    consultant_id: str | None,
    consultant_name: str | None,
    timeout_ms: int,
    trace: list[str],
) -> bool:
    if source_resume_id <= 0:
        return False

    async def _has_source_row() -> bool:
        probe = page.locator(f"a[href*='/edit/{int(source_resume_id)}/'], a[href*='/edit/{int(source_resume_id)}']").first
        try:
            return await probe.count() > 0
        except Exception:
            return False

    async def _clear_search_inputs() -> None:
        selectors = [
            "input[type='search']",
            "input[placeholder*='Search' i]",
            "input[placeholder*='Søk' i]",
            "input[placeholder*='Sok' i]",
            "input[aria-label*='Search' i]",
            "input[aria-label*='Søk' i]",
            "input[aria-label*='Sok' i]",
        ]
        for selector in selectors:
            inp = page.locator(selector).first
            try:
                if await inp.count() == 0 or not await inp.is_visible(timeout=180):
                    continue
                await inp.fill("", timeout=min(timeout_ms, 1400))
                await page.wait_for_timeout(120)
            except Exception:
                continue

    async def _click_scope_tab(labels: list[str]) -> bool:
        candidates: list[str] = []
        for label in labels:
            escaped = re.escape(label)
            candidates.extend(
                [
                    f"[role='tab']:has-text('{label}')",
                    f"button[role='tab']:has-text('{label}')",
                    f"button:has-text('{label}')",
                    f"a:has-text('{label}')",
                    f"text=/^\\s*{escaped}\\s*(\\(|$)/i",
                ]
            )
        for selector in candidates:
            tab = page.locator(selector).first
            try:
                if await tab.count() == 0 or not await tab.is_visible(timeout=220):
                    continue
                await tab.click(timeout=min(timeout_ms, 1600), force=True)
                await page.wait_for_timeout(450)
                return True
            except Exception:
                continue
        return False

    async def _click_left_nav_all() -> bool:
        # Prefer explicit left navigation item ("All" under CV in sidebar).
        js = """
        () => {
          const isVisible = (el) => {
            if (!el) return false;
            const s = window.getComputedStyle(el);
            if (s.visibility === 'hidden' || s.display === 'none') return false;
            const r = el.getBoundingClientRect();
            return r.width > 8 && r.height > 8;
          };
          const norm = (t) => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const nodes = Array.from(document.querySelectorAll('a,button,[role=\"button\"],[role=\"tab\"],li,div,span'));
          const cands = nodes.filter((n) => isVisible(n) && norm(n.textContent) === 'all');
          cands.sort((a,b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            const sa = (ra.left < 280 ? 0 : 10000) + Math.abs(ra.left - 80) + Math.abs(ra.top - 380);
            const sb = (rb.left < 280 ? 0 : 10000) + Math.abs(rb.left - 80) + Math.abs(rb.top - 380);
            return sa - sb;
          });
          for (const n of cands) {
            const r = n.getBoundingClientRect();
            if (r.left < 280 && r.top > 120 && r.bottom < window.innerHeight - 40) {
              n.click();
              return true;
            }
          }
          return false;
        }
        """
        try:
            clicked = await page.evaluate(js)
            if clicked:
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass
        return False

    async def _search_consultant_in_list() -> bool:
        terms = []
        if (consultant_name or "").strip():
            terms.append(str(consultant_name).strip())
        if (consultant_id or "").strip():
            terms.append(str(consultant_id).strip())
        terms.append(str(source_resume_id))

        selectors = [
            "input[type='search']",
            "input[placeholder*='Search' i]",
            "input[placeholder*='Søk' i]",
            "input[placeholder*='Sok' i]",
            "input[aria-label*='Search' i]",
            "input[aria-label*='Søk' i]",
            "input[aria-label*='Sok' i]",
        ]
        for selector in selectors:
            inp = page.locator(selector).first
            try:
                if await inp.count() == 0 or not await inp.is_visible(timeout=160):
                    continue
                for term in terms:
                    if not term:
                        continue
                    await inp.fill("", timeout=min(timeout_ms, 1200))
                    await inp.type(term, delay=16, timeout=min(timeout_ms, 2200))
                    await inp.press("Enter", timeout=min(timeout_ms, 1200))
                    await page.wait_for_timeout(700)
                    if await _has_source_row():
                        trace.append(f"Søk i CV-listen traff valgt kilde-CV (term='{term}')")
                        return True
                return False
            except Exception:
                continue
        return False

    async def _open_employee_profile_and_focus_cvs() -> bool:
        employee_urls = _employee_profile_urls(
            list_url=list_url,
            page_url=page.url,
            consultant_id=consultant_id,
            consultant_name=consultant_name,
        )
        if not employee_urls:
            return False
        for employee_url in employee_urls:
            try:
                await page.goto(employee_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
                await page.wait_for_timeout(900)
            except Exception:
                continue

            # Try to focus the CVs tab on employee page.
            for selector in [
                "a:has-text('CVs')",
                "button:has-text('CVs')",
                "text=/^\\s*CVs\\s*$/i",
                "a:has-text('CV')",
                "button:has-text('CV')",
            ]:
                tab = page.locator(selector).first
                try:
                    if await tab.count() == 0 or not await tab.is_visible(timeout=150):
                        continue
                    await tab.click(timeout=min(timeout_ms, 1800), force=True)
                    await page.wait_for_timeout(450)
                    break
                except Exception:
                    continue

            # Scroll towards CV table area when on overview page.
            for _ in range(4):
                if await _has_source_row():
                    trace.append(f"Fant kilde-CV i ansattvisning: {employee_url}")
                    return True
                try:
                    await page.mouse.wheel(0, 1400)
                except Exception:
                    pass
                await page.wait_for_timeout(220)

        return False

    if await _has_source_row():
        return True

    if list_url and ("/resumes" not in (page.url or "").lower() or "/edit/" in (page.url or "").lower()):
        try:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            await page.wait_for_timeout(900)
        except Exception:
            pass

    scoped_url = _consultant_resume_list_url(list_url=list_url, page_url=page.url, consultant_id=consultant_id)
    if scoped_url and scoped_url != (page.url or ""):
        try:
            await page.goto(scoped_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            await page.wait_for_timeout(900)
            trace.append(f"Byttet til konsulent-spesifikk CV-liste: {scoped_url}")
        except Exception:
            pass
        if await _has_source_row():
            return True

    # Employee page fallback (matches manual flow via Organisation -> Employees).
    if await _open_employee_profile_and_focus_cvs():
        return True

    for attempt in range(1, 5):
        if await _has_source_row():
            return True

        left_all = await _click_left_nav_all()
        if left_all:
            trace.append("Byttet til 'All' i venstremeny (CV)")
            if await _has_source_row():
                return True

        await _clear_search_inputs()
        clicked_all = await _click_scope_tab(["All", "Alle"])
        if clicked_all:
            trace.append("Byttet CV-liste til 'All/Alle' for å finne valgt kilde-CV")
            if await _has_source_row():
                return True

        searched = await _search_consultant_in_list()
        if searched and await _has_source_row():
            return True

        # Lazy-loaded lists: scroll a bit and retry.
        for _ in range(3):
            try:
                await page.mouse.wheel(0, 1400)
            except Exception:
                pass
            await page.wait_for_timeout(220)
            if await _has_source_row():
                return True

        if scoped_url:
            try:
                await page.goto(scoped_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
                await page.wait_for_timeout(500)
            except Exception:
                pass
            if await _has_source_row():
                return True

        if list_url:
            try:
                await page.goto(list_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
                await page.wait_for_timeout(700)
            except Exception:
                pass

        if attempt == 1:
            trace.append(
                f"Kilde-CV id={int(source_resume_id)} ikke synlig i første visning; "
                "prøver 'All/Alle' + filter-rydding."
            )

    return await _has_source_row()


def _absolutize_url(current_url: str, maybe_relative: str) -> str:
    parsed = urlparse(maybe_relative or "")
    if parsed.scheme and parsed.netloc:
        return maybe_relative
    base = _resume_list_url_from_current(current_url) or current_url
    return urljoin(base, maybe_relative)


def _slugify_name_for_url(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    replacements = {
        "æ": "ae",
        "ø": "o",
        "å": "a",
        "ä": "a",
        "ö": "o",
        "ü": "u",
        "é": "e",
        "è": "e",
        "ê": "e",
        "á": "a",
        "à": "a",
        "ó": "o",
        "ò": "o",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def _consultant_resume_list_url(*, list_url: str | None, page_url: str | None, consultant_id: str | None) -> str | None:
    # NOTE: `/resumes/user/{id}` is not a valid route in this Cinode tenant (returns 404).
    # Keep this disabled and use employee-profile flow as consultant context instead.
    _ = list_url
    _ = page_url
    _ = consultant_id
    return None


def _employee_profile_urls(*, list_url: str | None, page_url: str | None, consultant_id: str | None, consultant_name: str | None) -> list[str]:
    cid = str(consultant_id or "").strip()
    if not cid:
        return []
    base = (list_url or "").strip()
    if not base:
        base = _resume_list_url_from_current(page_url or "") or ""
    if not base:
        return []
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        return []
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return []
    tenant = parts[0]
    root = f"{parsed.scheme}://{parsed.netloc}/{tenant}/organisation/employees/{cid}"
    out = [root]
    slug = _slugify_name_for_url(consultant_name)
    if slug:
        out.append(f"{root}/{cid}-{slug}")
    return out


async def _open_newly_created_resume_editor(
    *,
    page: Any,
    list_url: str,
    expected_title: str,
    ids_before_create: set[int],
    timeout_ms: int,
    trace: list[str],
) -> None:
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)
    expected_norm = _normalize_title_text(expected_title)
    best: dict[str, Any] | None = None

    while asyncio.get_running_loop().time() < deadline:
        try:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
        except Exception:
            await page.wait_for_timeout(900)
            continue
        await page.wait_for_timeout(1200)
        if expected_norm:
            try:
                opened = await _try_open_editor_for_title(
                    page=page,
                    title=expected_title,
                    timeout_ms=min(timeout_ms, 8000),
                    trace=trace,
                )
                if opened:
                    trace.append(f"Åpnet ny CV via tittelmatch: '{expected_title}'")
                    return
            except Exception:
                pass
        links = await _collect_resume_links_from_list(page)
        if links:
            new_candidates = [row for row in links if int(row.get("id") or 0) not in ids_before_create]
            if new_candidates:
                new_candidates.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
                best = new_candidates[0]
            if best is None and expected_norm:
                title_matches = [
                    row
                    for row in links
                    if expected_norm in _normalize_title_text(str(row.get("title") or ""))
                    and int(row.get("id") or 0) not in ids_before_create
                ]
                if title_matches:
                    title_matches.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
                    best = title_matches[0]
            if best is None and expected_norm:
                title_matches_any = [
                    row
                    for row in links
                    if expected_norm in _normalize_title_text(str(row.get("title") or ""))
                ]
                if title_matches_any:
                    title_matches_any.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
                    best = title_matches_any[0]

        if best is not None:
            href = str(best.get("href") or "")
            target = _absolutize_url(page.url or list_url, href)
            trace.append(f"Valgte ny CV fra liste: id={best.get('id')} title='{best.get('title')}'")
            await page.goto(target, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            await page.wait_for_timeout(1200)
            return
        await page.wait_for_timeout(1000)

    raise CinodeBrowserCreateCvError(
        f"Fant ikke nyopprettet CV i listen etter oppretting (forventet tittel: '{expected_title}')"
    )


async def _open_resume_editor_by_id(
    *,
    page: Any,
    list_url: str,
    resume_id: int | None,
    timeout_ms: int,
    trace: list[str],
) -> None:
    if not resume_id or resume_id <= 0:
        raise CinodeBrowserCreateCvError("Mangler resume_id for åpning av riktig CV-editor")

    async def _try_open_via_transformed_href(base_href: str) -> bool:
        try:
            base_href = str(base_href or "")
            if not base_href:
                return False
            transformed = re.sub(r"/edit/\d+(/|$)", f"/edit/{int(resume_id)}\\1", base_href, flags=re.IGNORECASE)
            if transformed == base_href and "/edit/" in base_href.lower():
                transformed = re.sub(r"/edit/\d+", f"/edit/{int(resume_id)}", base_href, flags=re.IGNORECASE)
            if not transformed:
                return False
            target = _absolutize_url(page.url or list_url, transformed)
            await page.goto(target, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            await page.wait_for_timeout(900)
            return _extract_resume_id_from_url(page.url or "") == int(resume_id)
        except Exception:
            return False

    # Fast path: if current URL is an editor URL, try replacing only the resume id.
    try:
        cur = page.url or ""
        if "/edit/" in cur.lower():
            opened = await _try_open_via_transformed_href(cur)
            if opened:
                trace.append(f"Åpnet riktig CV-editor via transformert URL id={resume_id}")
                return
    except Exception:
        pass

    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        if _extract_resume_id_from_url(page.url or "") == int(resume_id):
            trace.append(f"Allerede i riktig CV-editor via id={resume_id}")
            return
        try:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            await page.wait_for_timeout(900)
        except Exception:
            await page.wait_for_timeout(600)
            continue
        link = page.locator(f"a[href*='/edit/{int(resume_id)}/'], a[href*='/edit/{int(resume_id)}']").first
        try:
            if await link.count() > 0:
                href = (await link.get_attribute("href")) or ""
                if href:
                    target = _absolutize_url(page.url or list_url, href)
                    await page.goto(target, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
                    await page.wait_for_timeout(900)
                    if _extract_resume_id_from_url(page.url or "") == int(resume_id):
                        trace.append(f"Åpnet riktig CV-editor via id={resume_id}")
                        return
        except Exception:
            pass
        # Fallback: transform any visible resume editor href to desired id.
        try:
            any_link = page.locator("a[href*='/resumes/user/'][href*='/edit/']").first
            if await any_link.count() > 0:
                any_href = (await any_link.get_attribute("href")) or ""
                if any_href and await _try_open_via_transformed_href(any_href):
                    trace.append(f"Åpnet riktig CV-editor via transformert liste-lenke id={resume_id}")
                    return
        except Exception:
            pass
        await page.wait_for_timeout(600)
    raise CinodeBrowserCreateCvError(f"Kunne ikke åpne riktig CV-editor via id={resume_id}")


async def _rename_resume_title_from_list_by_id(
    *,
    page: Any,
    list_url: str,
    resume_id: int | None,
    new_title: str,
    timeout_ms: int,
    trace: list[str],
) -> bool:
    if not resume_id or resume_id <= 0:
        return False
    title_value = (new_title or "").strip()
    if not title_value:
        return False

    deadline = asyncio.get_running_loop().time() + max(8.0, min(30.0, timeout_ms / 1000.0))
    row_link = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            await page.wait_for_timeout(900)
        except Exception:
            await page.wait_for_timeout(400)
            continue
        row_link = page.locator(f"a[href*='/edit/{int(resume_id)}/'], a[href*='/edit/{int(resume_id)}']").first
        try:
            if await row_link.count() > 0 and await row_link.is_visible(timeout=250):
                break
        except Exception:
            pass
        row_link = None
        await page.wait_for_timeout(500)
    if row_link is None:
        trace.append(f"Fant ikke ny CV-rad for rename (id={resume_id}); hopper over")
        return False

    row = row_link.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
    menu_opened = False
    try:
        row_buttons = row.locator("button")
        bcount = await row_buttons.count()
    except Exception:
        bcount = 0
    for idx in range(bcount - 1, max(-1, bcount - 4), -1):
        btn = row_buttons.nth(idx)
        try:
            if not await btn.is_visible(timeout=250):
                continue
            await btn.click(timeout=min(timeout_ms, 3000))
            await page.wait_for_timeout(300)
            menu_opened = True
            break
        except Exception:
            continue
    if not menu_opened:
        trace.append("Fant ikke 3-prikk meny for rename; hopper over")
        return False

    edit_title_candidates = [
        "button:has-text('Edit title')",
        "a:has-text('Edit title')",
        "text=/Edit\\s+title/i",
        "button:has-text('Rediger tittel')",
        "a:has-text('Rediger tittel')",
        "text=/Rediger\\s+tittel/i",
        "button:has-text('Edit')",
        "a:has-text('Edit')",
    ]
    clicked_edit_title = False
    for selector in edit_title_candidates:
        item = page.locator(selector).first
        try:
            if await item.count() == 0:
                continue
            if not await item.is_visible(timeout=300):
                continue
            await item.click(timeout=min(timeout_ms, 3000))
            await page.wait_for_timeout(350)
            clicked_edit_title = True
            break
        except Exception:
            continue
    if not clicked_edit_title:
        trace.append("Fant ikke 'Edit title' i meny; hopper over rename")
        return False

    # Modal or inline input
    input_selectors = [
        "input[name='title']",
        "input[name*='title' i]",
        "input[id*='title' i]",
        "input[placeholder*='Title' i]",
        "input[placeholder*='Titel' i]",
        "input:not([type='search'])",
    ]
    filled = False
    for selector in input_selectors:
        inp = page.locator(selector).first
        try:
            if await inp.count() == 0:
                continue
            if not await inp.is_visible(timeout=400):
                continue
            await inp.fill(title_value, timeout=min(timeout_ms, 5000))
            try:
                await inp.press("Enter", timeout=min(timeout_ms, 1200))
            except Exception:
                pass
            filled = True
            break
        except Exception:
            continue
    if not filled:
        trace.append("Fant ikke input for 'Edit title'; hopper over rename")
        return False

    try:
        await _click_first(
            page,
            [
                "button:has-text('Save')",
                "button:has-text('Lagre')",
                "button:has-text('Update')",
                "a:has-text('Save')",
                "a:has-text('Lagre')",
            ],
            timeout_ms=min(timeout_ms, 5000),
        )
        await page.wait_for_timeout(500)
    except Exception:
        pass
    trace.append(f"Satte tittel via 3-prikk meny: '{title_value}'")
    return True


async def _is_login_page(page: Any) -> bool:
    url = (page.url or "").lower()
    if "login" in url or "signin" in url or "logga" in url or "returnurl=" in url or "/start" in url:
        return True
    try:
        password_count = await page.locator("input[type='password']").count()
    except Exception:
        password_count = 0
    if password_count > 0:
        return True
    try:
        body_head = (await page.locator("body").inner_text())[:1000].lower()
    except Exception:
        body_head = ""
    login_hints = [
        "log in",
        "email address",
        "stay logged in",
        "no account",
        "click here",
    ]
    return any(hint in body_head for hint in login_hints)


async def _wait_for_portal_state(page: Any, timeout_ms: int, trace: list[str] | None = None) -> str:
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        if await _is_login_page(page):
            return "login"

        try:
            body_text = (await page.locator("body").inner_text()).strip()
        except Exception:
            body_text = ""
        try:
            buttons_count = await page.locator("button, a, [role='button']").count()
        except Exception:
            buttons_count = 0

        if body_text and len(body_text) > 40 and buttons_count >= 2:
            return "ready"

        await page.wait_for_timeout(900)

    if trace is not None:
        trace.append(f"Portal fortsatt ikke klar etter {timeout_ms} ms. URL: {page.url}")
    return "loading"


async def _wait_for_manual_login(page: Any, headless: bool, timeout_ms: int) -> None:
    if headless:
        raise CinodeBrowserCreateCvError(
            "Ikke innlogget i Cinode web i Playwright-profilen. Sett CINODE_UI_HEADLESS=false og logg inn i popup-vinduet."
        )

    await page.bring_to_front()
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        if not await _is_login_page(page):
            return
        await page.wait_for_timeout(1000)

    raise CinodeBrowserCreateCvError(
        "Innlogging tidsavbrudd. Logg inn i popup-vinduet og prøv igjen. Sesjonen lagres i .run/cinode-browser-profile."
    )


async def _click_create_cv(page: Any, timeout_ms: int) -> None:
    # Prefer the explicit plus-button next to the "CV" heading in the list view.
    plus_near_cv = [
        "xpath=(//main//*[normalize-space()='CV'])[1]/following::button[1]",
        "xpath=(//*[normalize-space()='CV'])[1]/following::button[1]",
    ]
    for selector in plus_near_cv:
        btn = page.locator(selector).first
        try:
            if await btn.count() == 0:
                continue
            if not await btn.is_visible(timeout=500):
                continue
            await btn.click(timeout=min(timeout_ms, 5000))
            await page.wait_for_timeout(300)
            return
        except Exception:
            continue

    candidates = [
        "button:has-text('CV +')",
        "a:has-text('CV +')",
        "button:has-text('+ CV')",
        "a:has-text('+ CV')",
        "button:has-text('Nytt CV')",
        "button:has-text('New CV')",
        "button:has-text('Create CV')",
        "button:has-text('Create resume')",
        "button:has-text('New resume')",
        "button[aria-label*='new cv' i]",
        "button[aria-label*='create cv' i]",
        "button[title*='new cv' i]",
        "button[title*='create cv' i]",
        "[data-testid='create-cv']",
        "[data-testid='new-resume']",
    ]
    try:
        await _click_first(page, candidates, timeout_ms=timeout_ms)
        return
    except Exception:
        pass

    text_patterns = [
        r"^\s*CV\s*\+\s*$",
        r"Nytt\s+CV",
        r"New\s+CV",
        r"Create\s+CV",
        r"Create\s+resume",
    ]
    for pattern in text_patterns:
        locator = page.locator(f"text=/{pattern}/i").first
        try:
            count = await locator.count()
        except Exception:
            count = 0
        if count == 0:
            continue
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.click(timeout=timeout_ms)
            return
        except Exception:
            continue

    # Fallback for newer UI: open Quick add and pick CV/Resume.
    quick_add_selectors = [
        "button:has-text('Quick add')",
        "a:has-text('Quick add')",
        "button[aria-label*='quick add' i]",
        "button[title*='quick add' i]",
    ]
    quick_opened = False
    for selector in quick_add_selectors:
        btn = page.locator(selector).first
        try:
            if await btn.count() == 0:
                continue
            if not await btn.is_visible(timeout=600):
                continue
            await btn.click(timeout=min(timeout_ms, 5000))
            await page.wait_for_timeout(350)
            quick_opened = True
            break
        except Exception:
            continue
    if quick_opened:
        quick_items = [
            "text=/^\\s*CV\\s*$/i",
            "text=/Resume/i",
            "text=/Nytt\\s+CV/i",
            "text=/New\\s+CV/i",
            "text=/Create\\s+CV/i",
            "text=/Create\\s+resume/i",
        ]
        for selector in quick_items:
            item = page.locator(selector).first
            try:
                if await item.count() == 0:
                    continue
                if not await item.is_visible(timeout=600):
                    continue
                await item.click(timeout=min(timeout_ms, 5000))
                return
            except Exception:
                continue

    raise CinodeBrowserCreateCvError("Ingen matchende knapp/element funnet")


async def _try_copy_selected_resume(
    *,
    page: Any,
    source_resume_id: int,
    consultant_id: str | None,
    consultant_name: str | None,
    title: str,
    ids_before_create: set[int],
    list_url: str | None,
    timeout_ms: int,
    trace: list[str],
) -> int | None:
    if source_resume_id <= 0:
        return None

    ids_before_local: set[int] = set(ids_before_create or set())
    trace.append(f"Forsøker å kopiere valgt kilde-CV (id={source_resume_id}) via meny")
    await page.wait_for_timeout(700)
    try:
        await _ensure_source_resume_visible_on_list(
            page=page,
            source_resume_id=int(source_resume_id),
            list_url=list_url,
            consultant_id=consultant_id,
            consultant_name=consultant_name,
            timeout_ms=min(timeout_ms, 30000),
            trace=trace,
        )
    except Exception:
        pass

    row = page.locator(f"a[href*='/edit/{source_resume_id}/']").first
    try:
        if await row.count() == 0:
            row = page.locator(f"a[href*='/edit/{source_resume_id}']").first
        if await row.count() == 0:
            trace.append(f"Fant ikke kilde-CV i listen for id={source_resume_id}; bruker 'opprett CV' fallback")
            return None
    except Exception:
        trace.append(f"Fant ikke kilde-CV i listen for id={source_resume_id}; bruker 'opprett CV' fallback")
        return None

    try:
        await row.scroll_into_view_if_needed(timeout=min(timeout_ms, 4000))
    except Exception:
        pass
    if not ids_before_local:
        try:
            links_now = await _collect_resume_links_from_list(page)
            ids_before_local = {int(r.get("id") or 0) for r in links_now if int(r.get("id") or 0) > 0}
            if ids_before_local:
                trace.append(f"Baseline-id for copy oppdatert fra side: {len(ids_before_local)}")
        except Exception:
            pass

    row_scope = row.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
    row_scope_alt = row.locator("xpath=ancestor::*[self::tr or self::li or self::div][2]")
    menu_selectors = [
        "button[aria-label*='more' i]",
        "button[title*='more' i]",
        "button[aria-label*='mer' i]",
        "button[title*='mer' i]",
        "button[aria-label*='menu' i]",
        "button[title*='menu' i]",
        "button[aria-haspopup='menu']",
        "[role='button'][aria-haspopup='menu']",
        "button:has([data-icon*='ellipsis' i])",
        "button:has-text('...')",
        "[role='button'][aria-label*='more' i]",
    ]
    menu_opened = False
    # First try: click the right-most button in the row (typically kebab-menu).
    for scope in [row_scope, row_scope_alt]:
        try:
            row_buttons = scope.locator("button")
            btn_count = await row_buttons.count()
        except Exception:
            btn_count = 0
        if btn_count > 0:
            for idx in range(btn_count - 1, max(btn_count - 4, -1), -1):
                btn = row_buttons.nth(idx)
                try:
                    if not await btn.is_visible(timeout=250):
                        continue
                    await btn.click(timeout=min(timeout_ms, 3000))
                    await page.wait_for_timeout(350)
                    menu_opened = True
                    trace.append("Åpnet radmeny via siste knapp i raden")
                    break
                except Exception:
                    continue
        if menu_opened:
            break

    for scope in [row_scope, row_scope_alt]:
        if menu_opened:
            break
        for selector in menu_selectors:
            btn = scope.locator(selector).first
            try:
                if await btn.count() == 0:
                    continue
                if not await btn.is_visible(timeout=400):
                    continue
                await btn.click(timeout=min(timeout_ms, 3000))
                await page.wait_for_timeout(350)
                menu_opened = True
                trace.append(f"Åpnet radmeny med selector: {selector}")
                break
            except Exception:
                continue
        if menu_opened:
            break

    if not menu_opened:
        # Try right-click on row to open context menu.
        try:
            await row.click(button="right", timeout=min(timeout_ms, 3000))
            await page.wait_for_timeout(350)
            menu_opened = True
            trace.append("Åpnet radmeny via høyreklikk")
        except Exception:
            pass

    if not menu_opened:
        trace.append("Fant ikke radmeny for kilde-CV; bruker 'opprett CV' fallback")
        return None

    copy_selectors = [
        "button:has-text('Copy')",
        "a:has-text('Copy')",
        "button:has-text('Copy CV')",
        "a:has-text('Copy CV')",
        "button:has-text('Duplicate')",
        "a:has-text('Duplicate')",
        "button:has-text('Kopiera')",
        "a:has-text('Kopiera')",
        "button:has-text('Kopier')",
        "a:has-text('Kopier')",
        "button:has-text('Kopi')",
        "a:has-text('Kopi')",
        "button:has-text('Dupl')",
        "a:has-text('Dupl')",
        "text=/^\\s*Copy\\s*$/i",
        "text=/Duplicate/i",
        "text=/Kopier/gi",
        "text=/Kopiera/gi",
        "text=/Kopi/gi",
        "text=/Dupl/gi",
    ]
    copied = False
    for selector in copy_selectors:
        item = page.locator(selector).first
        try:
            if await item.count() == 0:
                continue
            if not await item.is_visible(timeout=600):
                continue
            await item.click(timeout=min(timeout_ms, 5000))
            copied = True
            trace.append(f"Valgte kopieringshandling med selector: {selector}")
            break
        except Exception:
            continue
    if not copied:
        # Fallback: inspect menu items and click the most likely copy/duplicate item.
        try:
            menu_items = page.locator("[role='menuitem'], [role='option'], li[role='option'], button[role='menuitem']")
            count = await menu_items.count()
        except Exception:
            count = 0
        for idx in range(min(count, 20)):
            it = menu_items.nth(idx)
            try:
                if not await it.is_visible(timeout=250):
                    continue
                txt = _normalize_text((await it.inner_text()) or "")
                aria = _normalize_text((await it.get_attribute("aria-label")) or "")
                data_test = _normalize_text((await it.get_attribute("data-testid")) or "")
                hay = " ".join([txt, aria, data_test]).strip()
                if not hay:
                    continue
                if any(token in hay for token in ["copy", "duplicate", "kopi", "kopier", "kopiera", "dupl"]):
                    await it.click(timeout=min(timeout_ms, 5000))
                    copied = True
                    trace.append(f"Valgte kopieringshandling via menyitem-tekst: '{hay[:60]}'")
                    break
            except Exception:
                continue
    if not copied:
        # Debug helper: capture visible menu text so we can tune selectors quickly.
        try:
            menu_text = ""
            menus = page.locator("[role='menu']")
            if await menus.count() > 0:
                menu_text = (await menus.last.inner_text()).strip()
            if not menu_text:
                popover = page.locator("[role='menu'], [role='listbox'], [data-state='open']").last
                if await popover.count() > 0:
                    menu_text = (await popover.inner_text()).strip()
            if menu_text:
                menu_preview = re.sub(r"\s+", " ", menu_text)[:260]
                trace.append(f"Menyinnhold ved kopiering (preview): {menu_preview}")
        except Exception:
            pass
        trace.append("Fant ikke 'Copy/Duplicate' i radmeny; bruker 'opprett CV' fallback")
        return None

    await page.wait_for_timeout(500)
    try:
        dialogs = page.get_by_role("dialog")
        if await dialogs.count() > 0:
            root = dialogs.last
            try:
                await _fill_first(
                    root,
                    [
                        "input[name='title']",
                        "input[placeholder*='Title']",
                        "input[placeholder*='CV']",
                        "input[type='text']",
                    ],
                    title,
                    timeout_ms=min(timeout_ms, 5000),
                )
                trace.append("Satte tittel i kopieringsdialog")
            except Exception:
                trace.append("Fant ikke tittel-felt i kopieringsdialog; fortsetter")
            try:
                await _click_first(
                    root,
                    [
                        "button:has-text('Save')",
                        "button:has-text('Lagre')",
                        "button:has-text('Create')",
                        "button:has-text('Opprett')",
                        "button:has-text('Copy')",
                    ],
                    timeout_ms=min(timeout_ms, 7000),
                )
                trace.append("Bekreftet kopiering av CV i dialog")
            except Exception:
                trace.append("Fant ikke eksplisitt bekreft-knapp i kopieringsdialog; fortsetter")
    except Exception:
        pass

    # Some UIs use inline rename with a blinking title input; submit with Enter if visible.
    try:
        inline_title_inputs = page.locator("input:visible:not([type='search'])")
        icount = await inline_title_inputs.count()
    except Exception:
        icount = 0
    for idx in range(min(icount, 4)):
        inp = inline_title_inputs.nth(idx)
        try:
            placeholder = _normalize_text((await inp.get_attribute("placeholder")) or "")
            aria = _normalize_text((await inp.get_attribute("aria-label")) or "")
            name = _normalize_text((await inp.get_attribute("name")) or "")
            tags = " ".join([placeholder, aria, name])
            if "search" in tags:
                continue
            await inp.press("Enter", timeout=min(timeout_ms, 2000))
            trace.append("Bekreftet eventuell inline-navnsetting etter Copy (Enter)")
            break
        except Exception:
            continue

    # Verify copy actually created/opened a new resume before reporting success.
    deadline = asyncio.get_running_loop().time() + min(20.0, timeout_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        current_id = _extract_resume_id_from_url(page.url or "")
        if current_id and int(current_id) not in ids_before_local and int(current_id) != int(source_resume_id):
            trace.append(f"Copy verifisert via editor-url: ny resume id={current_id}")
            return int(current_id)
        try:
            links = await _collect_resume_links_from_list(page)
        except Exception:
            links = []
        if links:
            new_ids = [int(row.get("id") or 0) for row in links if int(row.get("id") or 0) not in ids_before_local]
            new_ids = [rid for rid in new_ids if rid > 0 and rid != int(source_resume_id)]
            # Guard: if we could not read baseline list, do not trust list-diff verification.
            # In that case we only trust editor-url verification above.
            if new_ids and ids_before_local:
                trace.append(f"Copy verifisert via CV-liste: nye id-er={new_ids[:3]}")
                return int(max(new_ids))
            if new_ids and not ids_before_local:
                trace.append(
                    "ADVARSEL: Fant kandidat-id(er) etter Copy, men baseline-listen var tom. "
                    "Ignorerer liste-verifisering for å unngå å velge feil (gammel) CV."
                )
        await page.wait_for_timeout(700)

    trace.append("Copy-klikk ga ingen ny CV i liste/editor innen timeout; bruker fallback til 'opprett CV'")
    return None


def _get_latest_open_page(context: Any) -> Any:
    pages = [p for p in context.pages if not p.is_closed()]
    if pages:
        return pages[-1]
    return None


async def _click_first(page: Any, selectors: list[str], timeout_ms: int) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            count = await locator.count()
        except Exception:
            count = 0
        if count == 0:
            continue
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.click(timeout=timeout_ms)
            return
        except Exception:
            continue
    raise CinodeBrowserCreateCvError("Ingen matchende knapp/element funnet")


async def _fill_first(page: Any, selectors: list[str], value: str, timeout_ms: int) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            count = await locator.count()
        except Exception:
            count = 0
        if count == 0:
            continue
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.fill(value, timeout=timeout_ms)
            return
        except Exception:
            continue
    raise CinodeBrowserCreateCvError("Fant ikke input-felt for CV-tittel")


async def _upload_docx(page: Any, file_path: str, timeout_ms: int) -> None:
    file_input = page.locator("input[type='file']").first
    count = await file_input.count()
    if count == 0:
        try:
            await _click_first(
                page,
                [
                    "button:has-text('Import')",
                    "button:has-text('Upload')",
                    "button:has-text('Word')",
                    "button:has-text('DOCX')",
                ],
                timeout_ms=timeout_ms,
            )
        except Exception:
            pass
    try:
        await file_input.set_input_files(file_path, timeout=timeout_ms)
    except Exception as exc:
        raise CinodeBrowserCreateCvError("Fant ikke filopplasting i Cinode-dialogen") from exc


async def _disable_generate_ai_checkbox(scope: Any, *, timeout_ms: int, trace: list[str]) -> None:
    candidates = [
        "text=/Do you want to use AI to generate the CV\\?/i",
        "text=/Generate CV with AI/i",
        "text=/Anv[äa]nd AI.*CV/i",
        "text=/Generera CV med AI/i",
    ]
    target_container = scope
    for selector in candidates:
        node = scope.locator(selector).first
        try:
            if await node.count() == 0:
                continue
            target_container = node.locator("xpath=ancestor::*[self::section or self::div][1]")
            break
        except Exception:
            continue

    # Try semantic switch first.
    switch = target_container.locator("[role='switch']").first
    try:
        if await switch.count() > 0 and await switch.is_visible(timeout=800):
            state = (await switch.get_attribute("aria-checked") or "").strip().lower()
            if state in {"true", "1"}:
                await switch.click(timeout=timeout_ms)
                await asyncio.sleep(0.3)
                trace.append("Generate CV with AI slått AV (switch)")
                return
    except Exception:
        pass

    # Fallback: checkbox in the same container.
    cb = target_container.locator("input[type='checkbox']").first
    try:
        if await cb.count() > 0 and await cb.is_visible(timeout=800):
            if await cb.is_checked():
                await cb.click(timeout=timeout_ms)
                await asyncio.sleep(0.3)
                trace.append("Generate CV with AI slått AV (checkbox)")
                return
            trace.append("Generate CV with AI var allerede AV")
            return
    except Exception:
        pass

    # Last fallback in dialog: first visible checkbox.
    any_cb = scope.locator("input[type='checkbox']").first
    try:
        if await any_cb.count() > 0 and await any_cb.is_visible(timeout=800) and await any_cb.is_checked():
            await any_cb.click(timeout=timeout_ms)
            await asyncio.sleep(0.3)
            trace.append("Generate CV with AI slått AV (fallback-checkbox)")
            return
    except Exception:
        pass

    trace.append("Fant ikke tydelig AI-toggle i ny CV-dialog")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _title_input_selectors() -> list[str]:
    return [
        "input[name='title']",
        "input[id*='title' i]",
        "input[name*='title' i]",
        "input[placeholder*='Title']",
        "input[placeholder*='Titel' i]",
        "input[placeholder*='CV']",
        "input[placeholder*='Name' i]",
        "input[placeholder*='Namn' i]",
        "input[placeholder*='Navn' i]",
        "input[aria-label*='Title']",
        "input[aria-label*='Titel' i]",
        "input[aria-label*='Name' i]",
        "input[aria-label*='Namn' i]",
        "input[aria-label*='Navn' i]",
    ]


async def _has_visible_title_input(scope: Any) -> bool:
    for selector in _title_input_selectors():
        loc = scope.locator(selector)
        try:
            count = await loc.count()
        except Exception:
            count = 0
        for idx in range(min(count, 4)):
            node = loc.nth(idx)
            try:
                if await node.is_visible(timeout=250):
                    return True
            except Exception:
                continue
    return False


async def _fill_title_input_fallback(scope: Any, value: str, timeout_ms: int, trace: list[str]) -> bool:
    # Last-resort fallback for tenants where title input has no stable attributes.
    candidates = scope.locator("input:not([type='hidden']):not([type='search'])")
    try:
        count = await candidates.count()
    except Exception:
        count = 0
    for idx in range(min(count, 10)):
        node = candidates.nth(idx)
        try:
            if not await node.is_visible(timeout=250):
                continue
            placeholder = _normalize_text((await node.get_attribute("placeholder")) or "")
            aria = _normalize_text((await node.get_attribute("aria-label")) or "")
            name = _normalize_text((await node.get_attribute("name")) or "")
            if any("search" in field for field in [placeholder, aria, name]):
                continue
            await node.fill(value, timeout=timeout_ms)
            trace.append("Fylte CV-tittel via fallback-input")
            return True
        except Exception:
            continue
    return False


async def _resolve_create_cv_root(page: Any, trace: list[str]) -> Any:
    root: Any = page
    try:
        dialogs = page.get_by_role("dialog")
        if await dialogs.count() > 0 and await dialogs.last.is_visible(timeout=200):
            root = dialogs.last
            trace.append("Fant modal-dialog for CV-oppretting")
    except Exception:
        root = page
    return root


async def _ensure_create_cv_form_open(page: Any, timeout_ms: int, trace: list[str]) -> Any:
    root = await _resolve_create_cv_root(page, trace)
    if await _has_visible_title_input(root):
        return root

    openers = [
        "button:has-text('CV +')",
        "a:has-text('CV +')",
        "xpath=(//main//*[normalize-space()='CV'])[1]/following::button[1]",
        "xpath=(//*[normalize-space()='CV'])[1]/following::button[1]",
        "xpath=//main//*[normalize-space()='CV']/following::button[1]",
        "xpath=//*[normalize-space()='CV']/following::button[1]",
        "button:has-text('Quick add')",
        "a:has-text('Quick add')",
    ]
    for selector in openers:
        btn = page.locator(selector).first
        try:
            if await btn.count() == 0:
                continue
            if not await btn.is_visible(timeout=400):
                continue
            await btn.click(timeout=min(timeout_ms, 4000))
            await page.wait_for_timeout(400)

            # If quick-add/menu opened, try to select CV item.
            cv_item_candidates = [
                "text=/^\\s*CV\\s*$/i",
                "text=/New\\s+CV/i",
                "text=/Nytt\\s+CV/i",
                "text=/Create\\s+CV/i",
                "text=/Resume/i",
            ]
            for item_selector in cv_item_candidates:
                item = page.locator(item_selector).first
                try:
                    if await item.count() == 0:
                        continue
                    if not await item.is_visible(timeout=250):
                        continue
                    await item.click(timeout=min(timeout_ms, 3000))
                    await page.wait_for_timeout(500)
                    break
                except Exception:
                    continue

            root = await _resolve_create_cv_root(page, trace)
            if await _has_visible_title_input(root):
                trace.append(f"Åpnet opprett-CV-skjema via selector: {selector}")
                return root
        except Exception:
            continue

    # Debug: snapshot visible non-search inputs to help tune selectors.
    try:
        inputs = page.locator("input:visible")
        n = await inputs.count()
        previews: list[str] = []
        for i in range(min(n, 6)):
            it = inputs.nth(i)
            ph = (await it.get_attribute("placeholder")) or ""
            ar = (await it.get_attribute("aria-label")) or ""
            nm = (await it.get_attribute("name")) or ""
            previews.append(_normalize_text(f"ph={ph} aria={ar} name={nm}")[:80])
        if previews:
            trace.append(f"Synlige input-felt ved oppretting: {' | '.join(previews)}")
    except Exception:
        pass
    return root


async def _configure_and_save_new_cv_dialog(
    *,
    page: Any,
    title: str,
    import_docx_path: str | None,
    require_docx_import: bool,
    disable_generate_ai: bool,
    preferred_language: str,
    preferred_template_keyword: str,
    timeout_ms: int,
    trace: list[str],
) -> bool:
    await page.wait_for_timeout(1000)
    root = await _ensure_create_cv_form_open(page=page, timeout_ms=min(timeout_ms, 15000), trace=trace)

    title_set = False
    try:
        await _fill_first(
            root,
            _title_input_selectors(),
            title,
            timeout_ms=min(timeout_ms, 15000),
        )
        title_set = True
    except CinodeBrowserCreateCvError:
        ok = await _fill_title_input_fallback(
            scope=root,
            value=title,
            timeout_ms=min(timeout_ms, 12000),
            trace=trace,
        )
        title_set = bool(ok)
        if not title_set:
            trace.append(
                "Fant ikke input-felt for CV-tittel; fortsetter med standardtittel fra Cinode-dialog."
            )

    imported_docx = False
    if disable_generate_ai:
        try:
            await _disable_generate_ai_checkbox(root, timeout_ms=min(timeout_ms, 6000), trace=trace)
        except Exception:
            trace.append("Kunne ikke verifisere AI-toggle i ny CV-dialog; fortsetter")

    if import_docx_path:
        try:
            await _upload_docx(root, import_docx_path, timeout_ms=min(timeout_ms, 15000))
            trace.append("DOCX lastet opp i ny CV-dialog")
            await page.wait_for_timeout(800)
            imported_docx = True
        except Exception as exc:
            if require_docx_import:
                trace.append(
                    "ADVARSEL: Fant ikke DOCX-opplasting i dialogen. "
                    "Kan ikke garantere at valgt utgangspunkt-CV blir brukt."
                )
                imported_docx = False
            else:
                trace.append("Fant ikke DOCX-opplasting i dialogen; fortsetter med tekstoppdatering i editor")
        except Exception:
            trace.append("Fant ikke DOCX-opplasting i dialogen; fortsetter med tekstoppdatering i editor")

    language_selected = await _select_option_from_selects(
        root,
        include_keywords=[preferred_language, "Norsk", "Norwegian"],
        exclude_keywords=[],
        timeout_ms=timeout_ms,
    )
    if language_selected:
        trace.append("Språk satt til Norwegian/Norsk")
    else:
        trace.append("Fant ikke språkvalg, fortsetter med eksisterende språk")

    keyword_norm = _normalize_text(preferred_template_keyword)
    exclude = []
    if "xlent" in keyword_norm:
        exclude = ["Differ", "Folden"]
    elif "differ" in keyword_norm:
        exclude = ["XLENT", "Folden"]
    elif "folden" in keyword_norm:
        exclude = ["XLENT", "Differ"]

    template_selected = await _select_option_from_selects(
        root,
        include_keywords=[preferred_template_keyword],
        exclude_keywords=exclude,
        timeout_ms=timeout_ms,
    )
    if template_selected:
        trace.append(f"Template satt med nøkkelord: {preferred_template_keyword}")
    else:
        trace.append("Fant ikke tydelig template-match i select-felt")

    await _click_first(
        root,
        [
            "button:has-text('Save')",
            "button:has-text('Lagre')",
            "button:has-text('Create')",
            "button:has-text('Opprett')",
            "button:has-text('Skapa')",
        ],
        timeout_ms=min(timeout_ms, 15000),
    )
    await page.wait_for_timeout(2000)
    try:
        dialogs = page.get_by_role("dialog")
        if await dialogs.count() > 0 and await dialogs.last.is_visible(timeout=300):
            dialog_text = (await dialogs.last.inner_text())[:220]
            raise CinodeBrowserCreateCvError(
                f"Ny CV-dialog lukket ikke etter lagring. Mulig valideringsfeil i felt. Dialog: {dialog_text}"
            )
    except CinodeBrowserCreateCvError:
        raise
    except Exception:
        pass
    return imported_docx


def _is_edit_url(url: str | None) -> bool:
    current = (url or "").lower()
    return "/edit/" in current and "/resumes/" in current


async def _is_resume_editor_page(page: Any) -> bool:
    current = (page.url or "").lower()
    if "login" in current or "signin" in current or "returnurl=" in current:
        return False
    if _is_edit_url(current):
        return True

    # Important: avoid treating the CV list page as editor.
    if "/resumes" in current and "/edit/" not in current:
        return False

    signals = 0
    try:
        if await page.locator("text=/Presentation|Presentasjon|Title and summary|Titel og sammendrag/i").count() > 0:
            signals += 1
    except Exception:
        pass
    try:
        if await page.locator("button:has-text('Preview'), a:has-text('Preview')").count() > 0:
            signals += 1
    except Exception:
        pass
    try:
        if await page.locator("button:has-text('Share'), a:has-text('Share')").count() > 0:
            signals += 1
    except Exception:
        pass
    try:
        if await page.locator("button:has-text('Save'), button:has-text('Lagre'), button:has-text('Spara')").count() > 0:
            signals += 1
    except Exception:
        pass

    if signals >= 3:
        return True
    if "/cv/" in current and signals >= 1:
        return True
    return False


async def _try_open_editor_for_title(*, page: Any, title: str, timeout_ms: int, trace: list[str]) -> bool:
    expected = (title or "").strip()
    if not expected:
        return False

    # Try clicking the created CV title first; this often opens its editor directly.
    title_candidates = [
        f"a:has-text('{expected}')",
        f"button:has-text('{expected}')",
        f"text={expected}",
    ]
    for selector in title_candidates:
        locator = page.locator(selector).first
        try:
            if await locator.count() == 0:
                continue
            await locator.wait_for(state="visible", timeout=min(timeout_ms, 4000))
            await locator.click(timeout=min(timeout_ms, 4000))
            await page.wait_for_timeout(1200)
            if _is_edit_url(page.url) or await _is_resume_editor_page(page):
                trace.append(f"Åpnet editor via CV-tittelmatch: {expected}")
                return True
        except Exception:
            continue

    # Fallback: locate title node, then click nearest edit control.
    title_node = page.locator(f"text={expected}").first
    try:
        if await title_node.count() > 0:
            container = title_node.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
            edit_candidates = [
                "button:has-text('Edit')",
                "a:has-text('Edit')",
                "button:has-text('Redigera')",
                "a:has-text('Redigera')",
                "button:has-text('Rediger')",
                "a:has-text('Rediger')",
                "button[aria-label*='Edit' i]",
                "button[title*='Edit' i]",
                "[role='button'][aria-label*='Edit' i]",
            ]
            for selector in edit_candidates:
                edit = container.locator(selector).first
                try:
                    if await edit.count() == 0:
                        continue
                    await edit.wait_for(state="visible", timeout=min(timeout_ms, 4000))
                    await edit.click(timeout=min(timeout_ms, 4000))
                    await page.wait_for_timeout(1200)
                    if _is_edit_url(page.url) or await _is_resume_editor_page(page):
                        trace.append(f"Åpnet editor via tittelrad + edit: {expected}")
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _normalize_title_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


async def _read_current_editor_title(page: Any) -> str:
    selectors = [
        "main h1",
        "h1",
        "[data-testid*='title' i]",
        ".page-title",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() == 0:
                continue
            text = (await locator.inner_text()).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _editor_matches_expected_title(page: Any, expected_title: str) -> bool:
    expected = _normalize_title_text(expected_title)
    if not expected:
        return True
    current_title = _normalize_title_text(await _read_current_editor_title(page))
    if current_title and (expected in current_title or current_title in expected):
        return True
    current_url = _normalize_title_text(page.url or "")
    tokens = [token for token in re.split(r"[^a-z0-9æøå]+", expected) if len(token) >= 4]
    if not tokens:
        tokens = [token for token in re.split(r"[^a-z0-9æøå]+", expected) if token]
    if not tokens:
        return False
    hits = sum(1 for token in tokens[:8] if token in current_url)
    needed = 2 if len(tokens) >= 3 else 1
    return hits >= needed


def _resume_list_url_from_current(current_url: str) -> str | None:
    try:
        parsed = urlparse(current_url)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return None
    company = parts[0]
    return f"{parsed.scheme}://{parsed.netloc}/{company}/resumes"


async def _open_resume_editor_page(*, page: Any, timeout_ms: int, trace: list[str], expected_title: str | None = None) -> None:
    if _is_edit_url(page.url) or await _is_resume_editor_page(page):
        if expected_title:
            if await _editor_matches_expected_title(page, expected_title):
                trace.append(f"Allerede i riktig CV-editor: {page.url}")
                return
            trace.append("Sikkerhetsvakt: feil CV-editor oppdaget, forsøker å bytte til riktig CV")
            resume_url = _resume_list_url_from_current(page.url or "")
            if resume_url:
                await page.goto(resume_url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
                await page.wait_for_timeout(1200)
            opened = await _try_open_editor_for_title(
                page=page,
                title=expected_title,
                timeout_ms=min(timeout_ms, 15000),
                trace=trace,
            )
            if opened and (await _editor_matches_expected_title(page, expected_title)):
                trace.append(f"Byttet til riktig CV-editor: {page.url}")
                return
            raise CinodeBrowserCreateCvError(
                f"Sikkerhetsstopp: kunne ikke verifisere riktig CV-editor for '{expected_title}'. "
                f"Aktiv side: {page.url}"
            )
        trace.append(f"Allerede i CV-editor: {page.url}")
        return

    if expected_title:
        opened = await _try_open_editor_for_title(
            page=page,
            title=expected_title,
            timeout_ms=min(timeout_ms, 15000),
            trace=trace,
        )
        if opened:
            return

    try:
        await _click_first(
            page,
            [
                "button:has-text('Edit')",
                "a:has-text('Edit')",
                "button:has-text('Redigera')",
                "a:has-text('Redigera')",
                "button:has-text('Rediger')",
                "a:has-text('Rediger')",
                "button[aria-label*='Edit' i]",
                "button[title*='Edit' i]",
                "[role='button'][aria-label*='Edit' i]",
            ],
            timeout_ms=min(timeout_ms, 15000),
        )
    except Exception as exc:
        if await _is_resume_editor_page(page):
            trace.append("Ingen separat Edit-knapp, men editor er allerede åpen")
            return
        raise CinodeBrowserCreateCvError("Fant ikke 'Edit'-knapp for nyopprettet CV") from exc

    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        if _is_edit_url(page.url) or await _is_resume_editor_page(page):
            if expected_title and not await _editor_matches_expected_title(page, expected_title):
                raise CinodeBrowserCreateCvError(
                    f"Sikkerhetsstopp: åpnet editor matcher ikke forventet CV-tittel '{expected_title}'. "
                    f"Aktiv side: {page.url}"
                )
            trace.append(f"Editor åpnet: {page.url}")
            return
        await page.wait_for_timeout(500)

    raise CinodeBrowserCreateCvError("CV-editor åpnet ikke etter klikk på Edit")


async def _update_resume_presentation_text(
    *,
    page: Any,
    presentation_text: str,
    timeout_ms: int,
    trace: list[str],
    strict_verify: bool = True,
    expected_title: str | None = None,
) -> None:
    content = (presentation_text or "").strip()
    if not content:
        trace.append("Ingen presentasjonstekst å skrive inn")
        return

    await _open_resume_editor_page(page=page, timeout_ms=timeout_ms, trace=trace, expected_title=expected_title)
    await page.wait_for_timeout(1200)

    filled = await _fill_presentation_area(
        page=page,
        value=content,
        timeout_ms=min(timeout_ms, 20000),
        trace=trace,
    )
    if not filled:
        filled = await _fill_first_textarea(page=page, value=content, timeout_ms=min(timeout_ms, 20000))
    if not filled:
        filled = await _fill_first_contenteditable(page=page, value=content, timeout_ms=min(timeout_ms, 20000))

    if not filled:
        try:
            await _click_first(
                page,
                [
                    "button:has-text('Presentation')",
                    "button:has-text('Presentasjon')",
                    "button:has-text('Professional')",
                    "button:has-text('Edit')",
                    "button:has-text('Redigera')",
                    "button:has-text('Rediger')",
                    "button[aria-label*='Edit' i]",
                    "button[title*='Edit' i]",
                    "button[aria-label*='Pen' i]",
                    "button[title*='Pen' i]",
                    "[role='button'][aria-label*='Edit' i]",
                    "[role='button'][title*='Edit' i]",
                    "[data-testid*='edit' i]",
                ],
                timeout_ms=min(timeout_ms, 15000),
            )
            await page.wait_for_timeout(900)
        except Exception:
            pass

        filled = await _fill_presentation_area(
            page=page,
            value=content,
            timeout_ms=min(timeout_ms, 20000),
            trace=trace,
        )
        if not filled:
            filled = await _fill_first_textarea(page=page, value=content, timeout_ms=min(timeout_ms, 20000))
        if not filled:
            filled = await _fill_first_contenteditable(page=page, value=content, timeout_ms=min(timeout_ms, 20000))
        if not filled:
            raise CinodeBrowserCreateCvError("Fant ikke tekstfelt for presentasjon i CV-editoren")

    try:
        await _click_first(
            page,
            [
                "button:has-text('Save')",
                "button:has-text('Lagre')",
                "button:has-text('Spara')",
                "a:has-text('Save')",
                "a:has-text('Lagre')",
                "a:has-text('Spara')",
                "[role='button']:has-text('Save')",
                "[role='button']:has-text('Lagre')",
                "[role='button']:has-text('Spara')",
            ],
            timeout_ms=min(timeout_ms, 12000),
        )
        await page.wait_for_timeout(1200)
        trace.append("Presentasjonstekst lagret i CV-editor")
    except Exception:
        # Some Cinode views autosave or expose save as contextual link only.
        trace.append("Fant ingen eksplisitt Save-knapp; fortsetter (autosave/blur kan være aktiv)")
        try:
            await page.keyboard.press("Tab")
        except Exception:
            pass
        await page.wait_for_timeout(800)

    verified = await _verify_presentation_contains(
        page=page,
        expected=content,
        timeout_ms=min(timeout_ms, 12000),
    )
    if not verified:
        message = (
            "Kunne ikke verifisere at ny presentasjonstekst ble lagret i editoren "
            f"({_text_fingerprint(content)})"
        )
        if strict_verify:
            raise CinodeBrowserCreateCvError(message)
        trace.append(f"ADVARSEL: {message}")


async def _rename_resume_title_in_editor(*, page: Any, new_title: str, timeout_ms: int, trace: list[str]) -> None:
    title_value = (new_title or "").strip()
    if not title_value:
        return
    selectors = [
        "input[name='title']",
        "input[name*='title' i]",
        "input[id*='title' i]",
        "input[aria-label*='title' i]",
        "textarea[name*='title' i]",
    ]
    renamed = False
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if await loc.count() == 0:
                continue
            if not await loc.is_visible(timeout=400):
                continue
            await loc.fill(title_value, timeout=min(timeout_ms, 5000))
            try:
                await loc.press("Enter", timeout=1200)
            except Exception:
                pass
            renamed = True
            break
        except Exception:
            continue
    if renamed:
        try:
            await _click_first(
                page,
                [
                    "button:has-text('Save')",
                    "button:has-text('Lagre')",
                    "button:has-text('Spara')",
                    "a:has-text('Save')",
                    "a:has-text('Lagre')",
                ],
                timeout_ms=min(timeout_ms, 6000),
            )
            await page.wait_for_timeout(600)
        except Exception:
            pass
        trace.append(f"Satte tittel på kopiert CV: '{title_value}'")
    else:
        trace.append("Fant ikke tydelig tittel-felt i editor; beholdt eksisterende tittel på kopiert CV")
        return


async def _fill_first_textarea(*, page: Any, value: str, timeout_ms: int) -> bool:
    selectors = [
        "textarea[name*='presentation' i]",
        "textarea[aria-label*='Presentation' i]",
        "textarea[placeholder*='Presentation' i]",
        "textarea[aria-label*='Presentasjon' i]",
        "textarea",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            count = 0
        if count == 0:
            continue
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible(timeout=800):
                    continue
                await candidate.fill(value, timeout=timeout_ms)
                try:
                    await candidate.dispatch_event("input")
                    await candidate.dispatch_event("change")
                except Exception:
                    pass
                return True
            except Exception:
                continue
    return False


async def _fill_presentation_area(*, page: Any, value: str, timeout_ms: int, trace: list[str]) -> bool:
    # Prefer textareas/contenteditable close to presentation headings to avoid
    # writing into unrelated fields in the editor.
    heading_selectors = [
        "text=/Presentation/i",
        "text=/Presentasjon/i",
        "text=/Title and summary/i",
        "text=/Titel og sammendrag/i",
    ]

    for heading_selector in heading_selectors:
        heading = page.locator(heading_selector).first
        try:
            if await heading.count() == 0:
                continue
        except Exception:
            continue

        try:
            container = heading.locator("xpath=ancestor::*[self::section or self::div][1]")
            textarea = container.locator("textarea").first
            if await textarea.count() > 0 and await textarea.is_visible(timeout=800):
                await textarea.fill(value, timeout=timeout_ms)
                try:
                    await textarea.dispatch_event("input")
                    await textarea.dispatch_event("change")
                except Exception:
                    pass
                trace.append("Oppdaterte presentasjon via nærliggende textarea")
                return True
        except Exception:
            pass

        try:
            contenteditable = container.locator("div[contenteditable='true'], [role='textbox'][contenteditable='true']").first
            if await contenteditable.count() > 0 and await contenteditable.is_visible(timeout=800):
                await contenteditable.click(timeout=timeout_ms)
                try:
                    await contenteditable.press("Control+A", timeout=timeout_ms)
                except Exception:
                    pass
                await page.keyboard.type(value, delay=0)
                trace.append("Oppdaterte presentasjon via nærliggende contenteditable")
                return True
        except Exception:
            pass

    return False


async def _fill_first_contenteditable(*, page: Any, value: str, timeout_ms: int) -> bool:
    selectors = [
        "div[contenteditable='true']",
        "[role='textbox'][contenteditable='true']",
        ".ql-editor",
        "[data-testid*='editor' i]",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            count = 0
        if count == 0:
            continue
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible(timeout=800):
                    continue
                await candidate.click(timeout=timeout_ms)
                try:
                    await candidate.press("Control+A", timeout=timeout_ms)
                except Exception:
                    pass
                await page.keyboard.type(value, delay=0)
                return True
            except Exception:
                continue
    return False


def _verification_probes(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []

    probes: list[str] = []
    # Start probe (often common with original text, but still useful).
    probes.append(cleaned[:180])
    # Middle probe.
    if len(cleaned) > 360:
        mid = len(cleaned) // 2
        start = max(0, mid - 90)
        probes.append(cleaned[start : start + 180])
    # End probe is critical because changes are often appended towards the end.
    if len(cleaned) > 220:
        probes.append(cleaned[-180:])

    out: list[str] = []
    seen: set[str] = set()
    for probe in probes:
        key = probe.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(probe)
    return out


async def _verify_presentation_contains(*, page: Any, expected: str, timeout_ms: int) -> bool:
    probes = _verification_probes(expected)
    if not probes:
        return True

    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)
    probe_lowers = [p.lower() for p in probes]
    while asyncio.get_running_loop().time() < deadline:
        # Check visible textarea values.
        try:
            textareas = page.locator("textarea")
            count = await textareas.count()
            for idx in range(min(count, 12)):
                candidate = textareas.nth(idx)
                try:
                    if not await candidate.is_visible(timeout=300):
                        continue
                    value = (await candidate.input_value()).strip().lower()
                    matches = sum(1 for probe in probe_lowers if probe in value)
                    if matches >= 2 or (len(probe_lowers) == 1 and matches == 1):
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # Check visible contenteditable values.
        try:
            editors = page.locator("div[contenteditable='true'], [role='textbox'][contenteditable='true'], .ql-editor")
            ecount = await editors.count()
            for idx in range(min(ecount, 12)):
                editor = editors.nth(idx)
                try:
                    if not await editor.is_visible(timeout=300):
                        continue
                    value = (await editor.inner_text()).strip().lower()
                    matches = sum(1 for probe in probe_lowers if probe in value)
                    if matches >= 2 or (len(probe_lowers) == 1 and matches == 1):
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        await page.wait_for_timeout(600)

    return False


async def _set_visible_switches_off(scope: Any, *, timeout_ms: int) -> int:
    changed = 0
    switch_locator = scope.locator("[role='switch']")
    try:
        switch_count = await switch_locator.count()
    except Exception:
        switch_count = 0
    for idx in range(min(switch_count, 20)):
        item = switch_locator.nth(idx)
        try:
            if not await item.is_visible(timeout=250):
                continue
            state = (await item.get_attribute("aria-checked") or "").strip().lower()
            if state in {"true", "1"}:
                await item.click(timeout=timeout_ms)
                changed += 1
        except Exception:
            continue

    checkbox_locator = scope.locator("input[type='checkbox']")
    try:
        cb_count = await checkbox_locator.count()
    except Exception:
        cb_count = 0
    for idx in range(min(cb_count, 20)):
        item = checkbox_locator.nth(idx)
        try:
            if not await item.is_visible(timeout=250):
                continue
            checked = await item.is_checked()
            if checked:
                await item.click(timeout=timeout_ms)
                changed += 1
        except Exception:
            continue
    return changed


async def _clear_text_inputs_in_scope(scope: Any, *, timeout_ms: int) -> int:
    cleared = 0
    textareas = scope.locator("textarea")
    try:
        count = await textareas.count()
    except Exception:
        count = 0
    for idx in range(min(count, 30)):
        item = textareas.nth(idx)
        try:
            if not await item.is_visible(timeout=250):
                continue
            await item.fill("", timeout=timeout_ms)
            try:
                await item.dispatch_event("input")
                await item.dispatch_event("change")
            except Exception:
                pass
            cleared += 1
        except Exception:
            continue

    editors = scope.locator("div[contenteditable='true'], [role='textbox'][contenteditable='true'], .ql-editor")
    try:
        ecount = await editors.count()
    except Exception:
        ecount = 0
    for idx in range(min(ecount, 20)):
        item = editors.nth(idx)
        try:
            if not await item.is_visible(timeout=250):
                continue
            await item.click(timeout=timeout_ms)
            try:
                await item.press("Control+A", timeout=timeout_ms)
            except Exception:
                pass
            await item.press("Backspace", timeout=timeout_ms)
            cleared += 1
        except Exception:
            continue
    return cleared


async def _clean_new_cv_sections(
    *,
    page: Any,
    timeout_ms: int,
    trace: list[str],
    expected_title: str | None = None,
) -> None:
    await _open_resume_editor_page(page=page, timeout_ms=timeout_ms, trace=trace, expected_title=expected_title)
    await page.wait_for_timeout(900)

    section_patterns = [
        r"Highlighted projects",
        r"Selected projects",
        r"Assignments in focus",
        r"Projects and assignments",
        r"Work experience",
        r"Valgt[e]?\s+prosjekt",
        r"Utvalgte\s+prosjekt",
        r"Prosjekter\s+og\s+oppdrag",
        r"Arbeidserfaring",
        r"Framh[aä]vda\s+projekt",
        r"Projekt\s+och\s+uppdrag",
    ]

    total_switched_off = 0
    total_cleared_fields = 0
    for pattern in section_patterns:
        heading = page.locator(f"text=/{pattern}/i").first
        try:
            if await heading.count() == 0:
                continue
            container = heading.locator("xpath=ancestor::*[self::section or self::div][1]")
            switched = await _set_visible_switches_off(container, timeout_ms=min(timeout_ms, 5000))
            cleared = await _clear_text_inputs_in_scope(container, timeout_ms=min(timeout_ms, 5000))
            total_switched_off += switched
            total_cleared_fields += cleared
        except Exception:
            continue

    # Save once after cleaning attempt.
    try:
        await _click_first(
            page,
            [
                "button:has-text('Save')",
                "button:has-text('Lagre')",
                "button:has-text('Spara')",
                "a:has-text('Save')",
                "a:has-text('Lagre')",
                "a:has-text('Spara')",
                "[role='button']:has-text('Save')",
                "[role='button']:has-text('Lagre')",
                "[role='button']:has-text('Spara')",
            ],
            timeout_ms=min(timeout_ms, 12000),
        )
    except Exception:
        pass
    await page.wait_for_timeout(700)
    trace.append(
        f"Clean mode utført: seksjon-toggle av={total_switched_off}, tekstfelt tømt={total_cleared_fields}"
    )


def _dedupe_skill_keywords(skills_keywords: list[str]) -> list[str]:
    def _strip_generated_suffix(value: str) -> str:
        txt = re.sub(r"\s+", " ", str(value or "").strip())
        if not txt:
            return ""
        m = re.match(r"^(.*?)[\s_\-]+\d{8,}$", txt)
        if m and m.group(1).strip():
            return m.group(1).strip()
        m2 = re.match(r"^(.*?[A-Za-zÆØÅæøå])\d{8,}$", txt)
        if m2 and m2.group(1).strip():
            return m2.group(1).strip()
        return txt

    out: list[str] = []
    seen: set[str] = set()
    for item in skills_keywords:
        value = _strip_generated_suffix(item)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _skill_keyword_variants(keyword: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(keyword or "").strip())
    if not raw:
        return []
    variants: list[str] = []
    seen: set[str] = set()
    has_compound_sep = any(ch in raw for ch in ["-", "/", "_"])

    def _push(v: str) -> None:
        v = re.sub(r"\s+", " ", v.strip())
        if not v:
            return
        key = v.lower()
        if key not in seen:
            seen.add(key)
            variants.append(v)

    _push(raw)
    _push(raw.replace("-", " "))
    _push(raw.replace("/", " "))
    _push(raw.replace("_", " "))
    _push(re.sub(r"[^\w\s\+\#]", " ", raw, flags=re.UNICODE))

    # Common normalization for known terms.
    low = raw.lower()
    if "claude" in low and ("code" in low or "cod" in low):
        _push("Claude-code")
        _push("Claude code")
        # Never degrade Claude+code requirements to overly broad "Claude".

    return variants[:5]


def _precreate_profile_skills_via_api(
    *,
    base_url: str | None,
    auth_value: str | None,
    company_id: str | None,
    consultant_id: str,
    keywords: list[str],
    trace: list[str],
) -> None:
    cleaned_base = (base_url or "").strip()
    cleaned_auth = (auth_value or "").strip()
    cleaned_company = (company_id or "").strip()
    cleaned_consultant = (consultant_id or "").strip()
    if not cleaned_base or not cleaned_auth or not cleaned_company or not cleaned_consultant:
        trace.append("API precreate skills hoppet over: mangler base/auth/company/user")
        return

    try:
        api_auth, _ = _resolve_api_authorization(cleaned_base, cleaned_auth)
    except Exception as exc:
        trace.append(f"API precreate skills hoppet over: auth-bootstrap feilet ({exc})")
        return

    ok_count = 0
    skipped_exists = 0
    failed: list[str] = []
    path = f"/v0.1/companies/{cleaned_company}/users/{cleaned_consultant}/profile/skills"
    for keyword in keywords:
        term = re.sub(r"\s+", " ", str(keyword or "").strip())
        if not term:
            continue
        payload = {"name": term, "level": 5, "saveTo": 3}
        try:
            status, data = _request_json(cleaned_base, api_auth, path, method="POST", body=payload)
        except Exception:
            failed.append(term)
            continue

        if 200 <= status < 300:
            ok_count += 1
            continue

        text = data if isinstance(data, str) else str(data)
        low = text.lower()
        if status in {400, 409} and (
            "already" in low
            or "exists" in low
            or "duplicate" in low
            or "finns redan" in low
            or "finnes allerede" in low
        ):
            skipped_exists += 1
            continue
        failed.append(term)

    trace.append(
        f"API precreate skills: opprettet={ok_count}, finnes={skipped_exists}, feilet={len(failed)}"
        + (f" ({', '.join(failed[:4])})" if failed else "")
    )


async def _open_skills_category_dialog(
    *,
    page: Any,
    timeout_ms: int,
    trace: list[str],
    force_reopen: bool = False,
) -> Any:
    async def _find_open_skills_scope() -> Any | None:
        drawers = page.locator("mat-sidenav.mat-drawer-opened, mat-drawer.mat-drawer-opened, [role='dialog']")
        try:
            dcount = await drawers.count()
        except Exception:
            dcount = 0
        for i in range(max(0, dcount - 1), -1, -1):
            d = drawers.nth(i)
            try:
                if not await d.is_visible(timeout=120):
                    continue
                txt = ((await d.inner_text()) or "").lower()
                if (
                    ("ferdigheter etter kategori" in txt or "skills by category" in txt)
                    and ("skills to include" in txt or "ferdigheter som skal inkluderes" in txt)
                ):
                    return d
            except Exception:
                continue
        return None

    # If requested, close current drawer and reopen cleanly.
    if force_reopen:
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(220)
        except Exception:
            pass
    # If drawer is already open from prior UI actions, reuse it.
    already_open = await _find_open_skills_scope()
    if already_open is not None and not force_reopen:
        trace.append("Skills-drawer allerede åpen; gjenbruker aktivt panel")
        return already_open

    # Deterministisk flyt (samme mønster som testscriptet):
    # 1) finn seksjonen "Ferdigheter etter kategori"
    # 2) finn "Teknikker"/"Produkter" under seksjonen
    # 3) klikk "Edit" i samme rad og returner åpen drawer
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(250)
    except Exception:
        pass
    section_scope: Any | None = None
    for _ in range(40):
        heading = page.locator("text=/Ferdigheter etter kategori|Skills by category/i").first
        try:
            if await heading.count() > 0 and await heading.is_visible(timeout=140):
                section_scope = heading.locator("xpath=ancestor::*[self::section or self::div][1]")
                break
        except Exception:
            pass
        try:
            await page.keyboard.press("PageDown")
        except Exception:
            await page.mouse.wheel(0, 700)
        await page.wait_for_timeout(200)
    if section_scope is None:
        raise RuntimeError("Fant ikke seksjonen 'Ferdigheter etter kategori' i editor")

    # Finn kategori-rad og klikk Edit i raden.
    target = None
    for pattern in [r"^Teknikker$", r"^Produkter$", r"Teknikker", r"Produkter", r"Techniques", r"Products"]:
        lbl = section_scope.locator(f"text=/{pattern}/i").first
        try:
            if await lbl.count() > 0 and await lbl.is_visible(timeout=220):
                target = lbl
                break
        except Exception:
            continue
    if target is None:
        for pattern in [r"^Teknikker$", r"^Produkter$", r"Teknikker", r"Produkter", r"Techniques", r"Products"]:
            lbl = page.locator(f"text=/{pattern}/i").first
            try:
                if await lbl.count() > 0 and await lbl.is_visible(timeout=220):
                    target = lbl
                    break
            except Exception:
                continue
    if target is None:
        raise RuntimeError("Fant ikke kategori-etikett 'Teknikker' eller 'Produkter' under seksjonen")

    try:
        await target.scroll_into_view_if_needed(timeout=min(timeout_ms, 5000))
        await page.wait_for_timeout(150)
    except Exception:
        pass

    for up in [3, 4, 5, 6, 7, 8]:
        row = target.locator(f"xpath=ancestor::*[{up}]")
        try:
            if await row.count() == 0:
                continue
            edit = row.locator("text=/^Edit$/i").first
            if await edit.count() > 0 and await edit.is_visible(timeout=220):
                await edit.click(timeout=min(timeout_ms, 3000))
                for _ in range(12):
                    await page.wait_for_timeout(220)
                    scope = await _find_open_skills_scope()
                    if scope is not None:
                        trace.append("Åpnet 'Ferdigheter etter kategori' via Edit-rad")
                        return scope
            # Fallback: pen/edit icon in same row.
            icon = row.locator(
                "button[aria-label*='edit' i], button[title*='edit' i], button[aria-label*='pen' i], button[title*='pen' i]"
            ).first
            if await icon.count() > 0 and await icon.is_visible(timeout=220):
                await icon.click(timeout=min(timeout_ms, 3000))
                for _ in range(12):
                    await page.wait_for_timeout(220)
                    scope = await _find_open_skills_scope()
                    if scope is not None:
                        trace.append("Åpnet 'Ferdigheter etter kategori' via penn-ikon")
                        return scope
        except Exception:
            continue

    raise RuntimeError("Fant 'Teknikker/Produkter' under seksjonen, men klarte ikke klikke 'Edit' i samme rad")


async def _add_and_check_skill_in_dialog(
    *,
    page: Any,
    dialog_scope: Any,
    keyword: str,
    timeout_ms: int,
) -> bool:
    def _norm(value: str) -> str:
        return " ".join((value or "").strip().lower().split())

    def _norm_skill(value: str) -> str:
        lowered = (value or "").strip().lower()
        lowered = lowered.replace("_", " ").replace("-", " ").replace("/", " ")
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    def _row_matches_alias(row_text: str, aliases: list[str]) -> bool:
        row_norm = _norm_skill(row_text)
        if not row_norm:
            return False
        for alias in aliases:
            alias_norm = _norm_skill(alias)
            if not alias_norm:
                continue
            # Strict phrase match with non-alnum boundaries.
            pattern = r"(?:^|[^a-z0-9])" + re.escape(alias_norm).replace(r"\ ", r"\s+") + r"(?:$|[^a-z0-9])"
            if re.search(pattern, row_norm):
                return True
        return False

    keyword = re.sub(r"\s+", " ", (keyword or "").strip())
    if not keyword:
        return False

    keyword_aliases: list[str] = []
    for cand in [keyword, keyword.replace("-", " "), keyword.replace(" ", "-")]:
        c = re.sub(r"\s+", " ", cand.strip())
        if c and c.lower() not in {x.lower() for x in keyword_aliases}:
            keyword_aliases.append(c)

    def _search_input(scope: Any) -> Any:
        return scope.locator(
            "input[placeholder*='Search' i], input[placeholder*='Søk' i], input[placeholder*='Sok' i], input[aria-label*='search' i], input[aria-label*='søk' i], input[aria-label*='sok' i]"
        ).first

    async def _ensure_category_for_skills() -> None:
        plus_probe = dialog_scope.locator(
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'skills to include') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'ferdigheter som skal inkluderes')]/following::button[contains(@class,'positive') or contains(@class,'button-icon')][1]"
        ).first
        try:
            if await plus_probe.count() > 0 and await plus_probe.is_visible(timeout=150):
                return
        except Exception:
            pass

        cat_input = dialog_scope.locator(
            "input[placeholder*='e.g. Roles' i], input[placeholder*='Select category' i], input[placeholder*='Kategori' i]"
        ).first
        try:
            if await cat_input.count() == 0 or not await cat_input.is_visible(timeout=400):
                return
        except Exception:
            return

        try:
            await cat_input.click(timeout=min(timeout_ms, 1200))
        except Exception:
            pass

        picked = False
        for sel in [
            "text=/^Teknikker$/i",
            "text=/^Techniques$/i",
            "text=/^Produkter$/i",
            "text=/^Products$/i",
            "[role='option']:has-text('Teknikker')",
            "[role='option']:has-text('Techniques')",
            "[role='option']:has-text('Produkter')",
            "[role='option']:has-text('Products')",
        ]:
            opt = page.locator(sel).first
            try:
                if await opt.count() == 0 or not await opt.is_visible(timeout=180):
                    continue
                await opt.click(timeout=min(timeout_ms, 1200), force=True)
                picked = True
                break
            except Exception:
                continue
        if not picked:
            try:
                await cat_input.fill("", timeout=min(timeout_ms, 1200))
                await cat_input.type("Teknikker", delay=15, timeout=min(timeout_ms, 1600))
                await cat_input.press("Enter", timeout=min(timeout_ms, 1200))
            except Exception:
                pass

        for _ in range(14):
            try:
                if await plus_probe.count() > 0 and await plus_probe.is_visible(timeout=120):
                    return
            except Exception:
                pass
            await page.wait_for_timeout(140)

    async def _set_level_five(container: Any) -> None:
        try:
            direct = container.locator("button:has-text('5'), [role='button']:has-text('5')").first
            if await direct.count() > 0 and await direct.is_visible(timeout=120):
                await direct.click(timeout=min(timeout_ms, 1600))
                await page.wait_for_timeout(120)
                return
        except Exception:
            pass

        try:
            edit_level = container.locator(
                "button[aria-label*='Edit level' i], button[title*='Edit level' i], button[aria-label*='Level' i], button[title*='Level' i]"
            ).first
            if await edit_level.count() > 0 and await edit_level.is_visible(timeout=120):
                await edit_level.click(timeout=min(timeout_ms, 1600))
                await page.wait_for_timeout(150)
        except Exception:
            pass
        try:
            popup = page.locator("text=/Level settings|Nivåinnstillinger/i").first
            scope = popup.locator("xpath=ancestor::div[1]") if await popup.count() > 0 else dialog_scope
            five = scope.locator("button:has-text('5'), [role='button']:has-text('5')").first
            if await five.count() > 0 and await five.is_visible(timeout=120):
                await five.click(timeout=min(timeout_ms, 1600))
                await page.wait_for_timeout(100)
        except Exception:
            pass

    async def _find_strict_row(scope: Any, limit: int = 120) -> Any | None:
        rows = scope.locator("li, tr, div")
        try:
            rcount = await rows.count()
        except Exception:
            rcount = 0
        for i in range(min(rcount, limit)):
            row = rows.nth(i)
            try:
                if not await row.is_visible(timeout=90):
                    continue
                txt = _norm((await row.inner_text()) or "")
                if not txt:
                    continue
                if "add skill" in txt or "skills to include" in txt or "ferdigheter som skal inkluderes" in txt:
                    continue
                if "select category" in txt or "select skills below" in txt:
                    continue
                if len(txt) > 320:
                    continue
                if not _row_matches_alias(txt, keyword_aliases):
                    continue
                controls = row.locator(
                    "input[type='checkbox'], [role='checkbox'], button:has(svg use[href*='check']), button[class*='positive'], button[aria-label*='select' i], button[title*='select' i], button[aria-label*='include' i], button[title*='include' i], button[class*='toggle'], button[class*='check']"
                )
                if await controls.count() == 0:
                    continue
                text_inputs = await row.locator("input[type='text']").count()
                if text_inputs > 0:
                    continue
                return row
            except Exception:
                continue
        return None

    async def _activate_row(row: Any) -> bool:
        cb = row.locator("input[type='checkbox'], [role='checkbox']").first
        if await cb.count() > 0 and await cb.is_visible(timeout=120):
            try:
                role = ((await cb.get_attribute("role")) or "").strip().lower()
                if role == "checkbox":
                    state = ((await cb.get_attribute("aria-checked")) or "").strip().lower()
                    if state not in {"true", "1"}:
                        await cb.click(timeout=min(timeout_ms, 1400))
                else:
                    checked = False
                    try:
                        checked = await cb.is_checked()
                    except Exception:
                        checked = False
                    if not checked:
                        await cb.check(timeout=min(timeout_ms, 1400))
            except Exception:
                return False
            await _set_level_five(row)
            return True
        toggle = row.locator(
            "button:has(svg use[href*='check']), button[class*='positive'], button[aria-label*='select' i], button[title*='select' i], button[aria-label*='include' i], button[title*='include' i], button[class*='toggle'], button[class*='check']"
        ).last
        if await toggle.count() > 0 and await toggle.is_visible(timeout=120):
            try:
                await toggle.click(timeout=min(timeout_ms, 1400), force=True)
                await _set_level_five(row)
                return True
            except Exception:
                return False
        return False

    try:
        level_popup = dialog_scope.locator("text=/Level settings|Nivåinnstillinger/i").first
        if await level_popup.count() > 0 and await level_popup.is_visible(timeout=120):
            close_btn = level_popup.locator("xpath=ancestor::div[1]").locator(
                "button[aria-label*='close' i], button[title*='close' i], button:has-text('×')"
            ).first
            if await close_btn.count() > 0 and await close_btn.is_visible(timeout=120):
                await close_btn.click(timeout=1200)
                await page.wait_for_timeout(180)
            else:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(180)
    except Exception:
        pass

    await _ensure_category_for_skills()

    try:
        for tab_text in ["All", "Alle"]:
            tab = dialog_scope.locator(f"text=/{tab_text}\\s*\\(/i").first
            if await tab.count() > 0 and await tab.is_visible(timeout=120):
                await tab.click(timeout=min(timeout_ms, 1200))
                await page.wait_for_timeout(120)
                break
    except Exception:
        pass
    try:
        await dialog_scope.evaluate("el => { el.scrollTop = 0; }")
        await page.wait_for_timeout(80)
    except Exception:
        pass
    try:
        search_inp = _search_input(dialog_scope)
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=120):
            await search_inp.fill("", timeout=min(timeout_ms, 1200))
            await page.wait_for_timeout(80)
    except Exception:
        pass

    strict_existing = await _find_strict_row(dialog_scope, limit=160)
    if strict_existing is not None:
        if await _activate_row(strict_existing):
            return True

    try:
        search_inp = _search_input(dialog_scope)
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=120):
            await search_inp.fill("", timeout=min(timeout_ms, 1200))
            await search_inp.type(keyword_aliases[0], delay=12, timeout=min(timeout_ms, 1800))
            await page.wait_for_timeout(180)
            strict_existing = await _find_strict_row(dialog_scope, limit=200)
            if strict_existing is not None and await _activate_row(strict_existing):
                return True
            await search_inp.fill("", timeout=min(timeout_ms, 1200))
            await page.wait_for_timeout(100)
    except Exception:
        pass

    label_patterns = [r"Skills to include", r"Ferdigheter som skal inkluderes"]
    plus_selectors = [
        "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'skills to include')]/following::button[contains(@class,'button-icon')][1]",
        "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'ferdigheter som skal inkluderes')]/following::button[contains(@class,'button-icon')][1]",
        "cui-button-icon[title*='Add' i] button",
        "button.button-icon--positive",
        "button[aria-label*='add' i]",
    ]

    include_scope = dialog_scope
    for pattern in label_patterns:
        label = dialog_scope.locator(f"text=/{pattern}/i").first
        try:
            if await label.count() == 0:
                continue
            include_scope = label.locator("xpath=ancestor::*[self::div or self::section or self::li][1]")
            break
        except Exception:
            continue

    async def _locate_add_skill_input() -> Any | None:
        # 1) Explicit scan of visible inputs to find the dedicated add-skill input.
        all_inputs = dialog_scope.locator("input")
        try:
            all_count = await all_inputs.count()
        except Exception:
            all_count = 0
        for i in range(min(all_count, 24)):
            inp = all_inputs.nth(i)
            try:
                if not await inp.is_visible(timeout=120):
                    continue
                ph = ((await inp.get_attribute("placeholder")) or "").strip().lower()
                ar = ((await inp.get_attribute("aria-label")) or "").strip().lower()
                nm = ((await inp.get_attribute("name")) or "").strip().lower()
                tags = " ".join([ph, ar, nm])
                if "search" in tags or "søk" in tags or "sok" in tags:
                    continue
                if "e.g. roles" in tags or "select category" in tags or "kategori" in tags:
                    continue
                if "try typing" in tags or "project management" in tags or "add skill" in tags:
                    return inp
            except Exception:
                continue

        selectors = [
            "input[placeholder*='Try typing' i]",
            "input[placeholder*='Project management' i]",
            "input[placeholder*='Add skill' i]",
            "input[placeholder*='Prøv å skrive' i]",
            "input[placeholder*='Legg til ferdighet' i]",
            "input[aria-label*='Add skill' i]",
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'add skill')]/following::input[1]",
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'skills to include')]/following::input[1]",
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'ferdigheter som skal inkluderes')]/following::input[1]",
        ]
        for selector in selectors:
            loc = dialog_scope.locator(selector)
            try:
                count = await loc.count()
            except Exception:
                count = 0
            for i in range(min(count, 8)):
                inp = loc.nth(i)
                try:
                    if not await inp.is_visible(timeout=220):
                        continue
                    ph = ((await inp.get_attribute("placeholder")) or "").strip().lower()
                    ar = ((await inp.get_attribute("aria-label")) or "").strip().lower()
                    # Avoid search box.
                    if "search" in ph or "search" in ar or "søk" in ph or "søk" in ar or "sok" in ph or "sok" in ar:
                        continue
                    return inp
                except Exception:
                    continue
        # Last fallback: any visible non-search text input in the drawer.
        generic = dialog_scope.locator("input[type='text'], input:not([type])")
        try:
            gcount = await generic.count()
        except Exception:
            gcount = 0
        for i in range(min(gcount, 12)):
            inp = generic.nth(i)
            try:
                if not await inp.is_visible(timeout=150):
                    continue
                ph = ((await inp.get_attribute("placeholder")) or "").strip().lower()
                ar = ((await inp.get_attribute("aria-label")) or "").strip().lower()
                nm = ((await inp.get_attribute("name")) or "").strip().lower()
                tags = " ".join([ph, ar, nm])
                if "search" in tags or "søk" in tags or "sok" in tags:
                    continue
                if "e.g. roles" in tags or "select category" in tags or "kategori" in tags:
                    continue
                return inp
            except Exception:
                continue
        return None

    typed_input = await _locate_add_skill_input()
    clicked_plus = typed_input is not None
    if not clicked_plus:
        for root in [dialog_scope, include_scope]:
            for selector in plus_selectors:
                plus = root.locator(selector).last
                try:
                    if await plus.count() == 0:
                        continue
                    if not await plus.is_visible(timeout=300):
                        continue
                    try:
                        await plus.click(timeout=min(timeout_ms, 2500))
                    except Exception:
                        await plus.click(timeout=min(timeout_ms, 2500), force=True)
                    await page.wait_for_timeout(300)
                    clicked_plus = True
                    break
                except Exception:
                    continue
            if clicked_plus:
                break
    if not clicked_plus:
        try:
            clicked_plus = await page.evaluate(
                """() => {
                    const roots = Array.from(document.querySelectorAll('mat-sidenav.mat-drawer-opened, mat-drawer.mat-drawer-opened, [role="dialog"], body'));
                    const isVisible = (el) => !!(el && el.getClientRects && el.getClientRects().length);
                    for (const root of roots) {
                        const nodes = Array.from(root.querySelectorAll('*'));
                        for (const n of nodes) {
                            const txt = (n.textContent || '').trim().toLowerCase();
                            if (!(txt.includes('skills to include') || txt.includes('ferdigheter som skal inkluderes'))) continue;
                            let p = n;
                            for (let i = 0; i < 8 && p; i++) {
                                const btns = Array.from(p.querySelectorAll('button,[role="button"]')).filter(isVisible);
                                if (btns.length > 0) { btns[btns.length - 1].click(); return true; }
                                p = p.parentElement;
                            }
                        }
                    }
                    return false;
                }"""
            )
            if clicked_plus:
                await page.wait_for_timeout(260)
        except Exception:
            clicked_plus = False
    if not clicked_plus:
        return False

    typed = False
    typed_value = ""
    for _ in range(6):
        inp = typed_input or await _locate_add_skill_input()
        try:
            if inp is not None and await inp.is_visible(timeout=350):
                await inp.fill("", timeout=min(timeout_ms, 1800))
                for cand in keyword_aliases:
                    await inp.fill("", timeout=min(timeout_ms, 1800))
                    await inp.type(cand, delay=20, timeout=min(timeout_ms, 3000))
                    await page.wait_for_timeout(120)
                    add_or_option = dialog_scope.locator(
                        f"text=/^(Add|Create)\\s+{re.escape(cand)}$/i, [role='option']:has-text('{cand}'), li:has-text('{cand}')"
                    ).first
                    if await add_or_option.count() > 0:
                        typed_value = cand
                        break
                if not typed_value:
                    typed_value = keyword_aliases[0]
                typed = True
                typed_input = inp
                break
        except Exception:
            pass
        try:
            plus_retry = dialog_scope.locator(
                "cui-button-icon[title*='Add' i] button, button.button-icon--positive"
            ).first
            if await plus_retry.count() > 0 and await plus_retry.is_visible(timeout=150):
                await plus_retry.click(timeout=1200, force=True)
                await page.wait_for_timeout(220)
        except Exception:
            pass
    if not typed:
        return False

    add_clicked = False
    add_selectors: list[str] = []
    for cand in keyword_aliases:
        add_selectors.extend(
            [
                f"text=/^Add\\s+{re.escape(cand)}$/i",
                f"text=/^Create\\s+{re.escape(cand)}$/i",
            ]
        )
    for selector in add_selectors:
        node = dialog_scope.locator(selector).first
        try:
            if await node.count() > 0 and await node.is_visible(timeout=160):
                await node.click(timeout=min(timeout_ms, 1800))
                await page.wait_for_timeout(180)
                add_clicked = True
                break
        except Exception:
            continue
    if not add_clicked and typed_input is not None:
        try:
            box = await typed_input.bounding_box()
        except Exception:
            box = None
        if box:
            try:
                await page.mouse.click(box["x"] + box["width"] - 8, box["y"] + (box["height"] / 2))
                await page.wait_for_timeout(280)
                add_clicked = True
            except Exception:
                pass
    if not add_clicked:
        # Continue anyway: when keyword already exists, typed filter can still make row selectable.
        await page.wait_for_timeout(140)

    await page.wait_for_timeout(500)

    try:
        for tab_text in ["All", "Alle"]:
            all_tab = dialog_scope.locator(f"text=/{tab_text}\\s*\\(/i").first
            if await all_tab.count() > 0 and await all_tab.is_visible(timeout=120):
                await all_tab.click(timeout=min(timeout_ms, 1400))
                await page.wait_for_timeout(180)
                break
    except Exception:
        pass

    try:
        search_inp = _search_input(dialog_scope)
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=150):
            await search_inp.fill("", timeout=min(timeout_ms, 1200))
            await search_inp.type(typed_value or keyword_aliases[0], delay=15, timeout=min(timeout_ms, 2400))
            await page.wait_for_timeout(120)
    except Exception:
        pass

    async def _find_row() -> Any | None:
        return await _find_strict_row(dialog_scope, limit=220)

    row = None
    for _ in range(4):
        row = await _find_row()
        if row is not None:
            break
        await page.wait_for_timeout(180)
    if row is None:
        return False

    if not await _activate_row(row):
        return False
    try:
        selected_tab = dialog_scope.locator("text=/Selected\\s*\\(/i").first
        if await selected_tab.count() > 0 and await selected_tab.is_visible(timeout=120):
            await selected_tab.click(timeout=1200)
            await page.wait_for_timeout(180)
        selected_rows = dialog_scope.locator("li, tr, div")
        scount = await selected_rows.count()
        for i in range(min(scount, 120)):
            sr = selected_rows.nth(i)
            try:
                if not await sr.is_visible(timeout=80):
                    continue
                txt = (await sr.inner_text()) or ""
                if _row_matches_alias(txt, keyword_aliases):
                    return True
            except Exception:
                continue
        # If tab/content doesn't expose selected rows clearly, rely on successful activation.
        return True
    except Exception:
        return True


async def _update_resume_skills_keywords(
    *,
    page: Any,
    skills_keywords: list[str],
    timeout_ms: int,
    trace: list[str],
    expected_title: str | None = None,
    strict_mode: bool = False,
    cinode_api_base_url: str | None = None,
    cinode_api_auth_value: str | None = None,
    cinode_company_id: str | None = None,
    consultant_id: str | None = None,
) -> None:
    keywords = _dedupe_skill_keywords(skills_keywords)[:40]
    if not keywords:
        trace.append("Ingen skills/keywords å legge til")
        return
    trace.append(
        f"Skills/keywords kandidater ({len(keywords)}): {', '.join(keywords[:10])}"
        + (" [strict]" if strict_mode else "")
    )
    if consultant_id:
        try:
            await asyncio.to_thread(
                _precreate_profile_skills_via_api,
                base_url=cinode_api_base_url,
                auth_value=cinode_api_auth_value,
                company_id=cinode_company_id,
                consultant_id=consultant_id,
                keywords=keywords,
                trace=trace,
            )
        except Exception as exc:
            trace.append(f"API precreate skills hoppet over pga feil: {exc}")

    await _open_resume_editor_page(page=page, timeout_ms=timeout_ms, trace=trace, expected_title=expected_title)
    await page.wait_for_timeout(700)
    resume_id = _extract_resume_id_from_url(page.url or "")
    if resume_id:
        trace.append(f"Skills-flyt: bruker aktiv CV-editor direkte (id={resume_id})")
    try:
        dialog_scope = await _open_skills_category_dialog(
            page=page,
            timeout_ms=min(timeout_ms, 16000),
            trace=trace,
        )
    except Exception as exc:
        trace.append(f"Kunne ikke åpne skills-dialog: {exc}")
        return
    # Hard guard: if drawer/dialog wasn't opened, stop keyword loop early.
    try:
        scope_text = ((await dialog_scope.inner_text()) or "").lower()
    except Exception:
        scope_text = ""
    if "skills to include" not in scope_text and "ferdigheter som skal inkluderes" not in scope_text:
        trace.append("Skills-drawer ikke åpnet; hopper over keyword-loop for å unngå feilklikk.")
        return

    added = 0
    activated = 0
    failed: list[str] = []
    deadline = asyncio.get_running_loop().time() + (45.0 if strict_mode else 55.0)
    for keyword in keywords:
        if asyncio.get_running_loop().time() > deadline:
            failed.append("timeout")
            trace.append("Avbrøt keyword-oppdatering pga tidsgrense (55s)")
            break
        ok = False
        used_variant = ""

        variants = _skill_keyword_variants(keyword)
        if strict_mode:
            ordered: list[str] = []
            seen_local: set[str] = set()
            for cand in [keyword, keyword.replace("-", " "), keyword.replace(" ", "-"), *variants]:
                vv = re.sub(r"\s+", " ", (cand or "").strip())
                if not vv:
                    continue
                key = vv.lower()
                if key in seen_local:
                    continue
                seen_local.add(key)
                ordered.append(vv)
            variants = ordered[:6]

        # Pass 1: use current open dialog scope.
        for variant in variants:
            ok = await _add_and_check_skill_in_dialog(
                page=page,
                keyword=variant,
                dialog_scope=dialog_scope,
                timeout_ms=min(timeout_ms, 5000),
            )
            if ok:
                used_variant = variant
                break

        # Pass 2 (recovery): re-open dialog in fresh UI state and retry once.
        if not ok:
            try:
                dialog_scope = await _open_skills_category_dialog(
                    page=page,
                    timeout_ms=min(timeout_ms, 12000),
                    trace=trace,
                    force_reopen=True,
                )
                trace.append(f"Retry i ren dialog-state for keyword: {keyword}")
                for variant in variants:
                    ok = await _add_and_check_skill_in_dialog(
                        page=page,
                        keyword=variant,
                        dialog_scope=dialog_scope,
                        timeout_ms=min(timeout_ms, 5000),
                    )
                    if ok:
                        used_variant = variant
                        break
            except Exception:
                pass
        if ok:
            added += 1
            activated += 1
            if used_variant and used_variant.lower() != keyword.lower():
                trace.append(f"Skill lagt inn via variant: '{keyword}' -> '{used_variant}'")
            await page.wait_for_timeout(120)
        else:
            failed.append(keyword)

    if failed:
        trace.append(
            f"La til {added} skills/keywords (aktivert: {activated}). "
            f"Fant ikke input for: {', '.join(failed[:8])}"
        )
    else:
        trace.append(f"La til {added} skills/keywords i Teknikker/Verktøy (aktivert: {activated})")

    # Try save after skill updates (best effort), preferring the open skills drawer.
    try:
        saved = False
        for selector in [
            "button:has-text('Save')",
            "button:has-text('Lagre')",
            "button:has-text('Spara')",
            "a:has-text('Save')",
            "a:has-text('Lagre')",
        ]:
            btn = dialog_scope.locator(selector).first
            try:
                if await btn.count() == 0 or not await btn.is_visible(timeout=200):
                    continue
                try:
                    await btn.click(timeout=min(timeout_ms, 3000))
                except Exception:
                    await btn.click(timeout=min(timeout_ms, 3000), force=True)
                await page.wait_for_timeout(700)
                saved = True
                break
            except Exception:
                continue
        if not saved:
            await _click_first(
                page,
                [
                    "button:has-text('Save')",
                    "button:has-text('Lagre')",
                    "button:has-text('Spara')",
                    "a:has-text('Save')",
                    "a:has-text('Lagre')",
                ],
                timeout_ms=min(timeout_ms, 8000),
            )
            await page.wait_for_timeout(700)
        trace.append("Lagret etter skills/keywords-oppdatering")
    except Exception:
        trace.append("Fant ingen eksplisitt Save etter skills/keywords (kan være autosave)")


async def _select_option_from_selects(
    root: Any,
    *,
    include_keywords: list[str],
    exclude_keywords: list[str],
    timeout_ms: int,
) -> bool:
    include = [_normalize_text(item) for item in include_keywords if item.strip()]
    exclude = [_normalize_text(item) for item in exclude_keywords if item.strip()]
    selects = root.locator("select")
    try:
        select_count = await selects.count()
    except Exception:
        select_count = 0
    if select_count == 0:
        return False

    for idx in range(select_count):
        select = selects.nth(idx)
        try:
            option_locator = select.locator("option")
            option_count = await option_locator.count()
        except Exception:
            option_count = 0
        if option_count == 0:
            continue

        best_label: str | None = None
        best_score = -1
        for option_idx in range(option_count):
            option = option_locator.nth(option_idx)
            try:
                label_raw = (await option.inner_text()).strip()
            except Exception:
                continue
            label_norm = _normalize_text(label_raw)
            if not label_norm:
                continue
            if exclude and any(token in label_norm for token in exclude):
                continue
            score = 0
            for token in include:
                if token and token in label_norm:
                    score += 1
            if score <= 0:
                continue
            if score > best_score:
                best_score = score
                best_label = label_raw

        if not best_label:
            continue

        try:
            await select.select_option(label=best_label, timeout=min(timeout_ms, 10000))
            return True
        except Exception:
            try:
                matching_option = select.locator("option", has_text=best_label).first
                value = await matching_option.get_attribute("value")
                if value is None:
                    continue
                await select.select_option(value=value, timeout=min(timeout_ms, 10000))
                return True
            except Exception:
                continue

    return False
