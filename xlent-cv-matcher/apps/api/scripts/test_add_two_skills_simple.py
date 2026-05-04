from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.cinode_directory import (  # type: ignore
    _decode_company_id_from_jwt,
    _request_json,
    _resolve_api_authorization,
    test_cinode_credential,
)

START_URL = "https://app.cinode.com/xlent/resumes"
SOURCE_USER_ID = 304293  # Thomas Elboth
SOURCE_CV_TITLE = "Lead Data Scientist"
SOURCE_RESUME_ID = 439810
NEW_CV_TITLE = "test_cv_skills"
SKILLS = ["skill1", "skill2"]
TIMEOUT_MS = 30000
HEADLESS = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _load_cinode_auth() -> tuple[str, str]:
    base_url = os.getenv("CINODE_BASE_URL", "").strip()
    auth_value = os.getenv("CINODE_API_TOKEN", "").strip()
    if base_url and auth_value:
        return base_url, auth_value

    env_candidates = [
        API_ROOT / ".env",
        _repo_root() / ".env",
        Path.cwd() / ".env",
    ]
    for env_path in env_candidates:
        env_values = _read_env_file(env_path)
        if not base_url:
            base_url = env_values.get("CINODE_BASE_URL", "").strip()
        if not auth_value:
            auth_value = env_values.get("CINODE_API_TOKEN", "").strip()
        if base_url and auth_value:
            break

    if not base_url:
        base_url = "https://api.cinode.com"
    if not auth_value:
        raise RuntimeError("Mangler CINODE_API_TOKEN i miljøvariabler/.env")
    return base_url, auth_value


def _resolve_company_id(base_url: str, raw_auth: str, api_auth: str) -> str:
    ok, _, _, whoami = test_cinode_credential(base_url, raw_auth)
    if ok and isinstance(whoami, dict):
        for key in ["companyId", "CompanyId"]:
            if whoami.get(key) is not None:
                return str(whoami[key])

    jwt_company_id = _decode_company_id_from_jwt(raw_auth)
    if jwt_company_id:
        return str(jwt_company_id)

    status, data = _request_json(base_url, api_auth, "/v0.1/companies", method="GET")
    if 200 <= status < 300:
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and first.get("id") is not None:
                return str(first["id"])
        if isinstance(data, dict):
            for key in ["id", "companyId", "CompanyId"]:
                if data.get(key) is not None:
                    return str(data[key])
            companies = data.get("companies")
            if isinstance(companies, list) and companies:
                first_company = companies[0]
                if isinstance(first_company, dict) and first_company.get("id") is not None:
                    return str(first_company["id"])

    snippet = data if isinstance(data, str) else str(data)
    raise RuntimeError(f"Kunne ikke finne companyId via Cinode API. Status={status} body={snippet[:220]}")


def _ensure_skills_exist_via_api(skills: list[str]) -> None:
    wanted = [s.strip() for s in skills if s and s.strip()]
    if not wanted:
        return
    deduped: list[str] = []
    seen: set[str] = set()
    for skill in wanted:
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(skill)

    base_url, raw_auth = _load_cinode_auth()
    api_auth, _ = _resolve_api_authorization(base_url, raw_auth)
    company_id = _resolve_company_id(base_url, raw_auth, api_auth)
    _log(f"Cinode bootstrap OK: company_id={company_id}, user_id={SOURCE_USER_ID}")

    path = f"/v0.1/companies/{company_id}/users/{SOURCE_USER_ID}/profile/skills"
    for skill in deduped:
        payload = {"name": skill, "level": 5, "saveTo": 3}
        status, data = _request_json(base_url, api_auth, path, method="POST", body=payload)
        if 200 <= status < 300:
            _log(f"API pre-create skill OK: {skill}")
            continue

        text = data if isinstance(data, str) else str(data)
        lowered = text.lower()
        if status in {400, 409} and (
            "already" in lowered
            or "exists" in lowered
            or "duplicate" in lowered
            or "finns redan" in lowered
            or "finnes allerede" in lowered
        ):
            _log(f"API pre-create skill finnes allerede: {skill}")
            continue

        raise RuntimeError(f"API pre-create feilet for skill '{skill}': HTTP {status} body={text[:260]}")


async def _open_resume_list(page: Any) -> None:
    await page.goto(START_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    # Wait for list-like content to appear (spinner can take long time in Cinode).
    for _ in range(2):
        for _ in range(50):
            try:
                anchors = page.locator("a[href*='/resumes/user/'][href*='/edit/']")
                if await anchors.count() > 0:
                    return
            except Exception:
                pass
            await page.wait_for_timeout(220)
        await page.reload(wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        await page.wait_for_timeout(700)

    # Last check with user-specific pattern before giving up.
    try:
        anchors2 = page.locator(f"a[href*='/resumes/user/{SOURCE_USER_ID}/'][href*='/edit/']")
        if await anchors2.count() > 0:
            return
    except Exception:
        pass
    raise RuntimeError("CV-listen lastet ikke inn (ingen edit-lenker funnet)")


async def _collect_resume_ids(page: Any) -> set[int]:
    ids: set[int] = set()
    links = page.locator(f"a[href*='/resumes/user/{SOURCE_USER_ID}/edit/']")
    try:
        count = await links.count()
    except Exception:
        count = 0
    for i in range(min(count, 300)):
        a = links.nth(i)
        try:
            href = (await a.get_attribute("href")) or ""
            m = re.search(r"/edit/(\d+)", href)
            if m:
                ids.add(int(m.group(1)))
        except Exception:
            continue
    return ids


async def _find_cv_row(page: Any, cv_title: str) -> Any:
    # 1) Prefer hardcoded Thomas-user + source resume id (deterministic for this test).
    by_id = page.locator(
        f"a[href*='/resumes/user/{SOURCE_USER_ID}/edit/{SOURCE_RESUME_ID}/'], "
        f"a[href*='/resumes/user/{SOURCE_USER_ID}/edit/{SOURCE_RESUME_ID}']"
    ).first
    if await by_id.count() > 0 and await by_id.is_visible(timeout=1200):
        row = by_id.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
        if await row.count() > 0:
            return row

    # 1b) Fallback by resume id only.
    by_resume = page.locator(f"a[href*='/edit/{SOURCE_RESUME_ID}/'], a[href*='/edit/{SOURCE_RESUME_ID}']").first
    if await by_resume.count() > 0 and await by_resume.is_visible(timeout=1200):
        row = by_resume.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
        if await row.count() > 0:
            return row

    label = page.locator(f"text=/{re.escape(cv_title)}/i").first
    if await label.count() > 0 and await label.is_visible(timeout=1200):
        row = label.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
        if await row.count() > 0:
            return row

    # Fallback: locate by known URL slug when title text is not rendered in row.
    slug = re.sub(r"[^a-z0-9]+", "-", cv_title.lower()).strip("-")
    anchor = page.locator(f"a[href*='/{slug}'], a[href*='{slug}']").first
    if await anchor.count() > 0 and await anchor.is_visible(timeout=1200):
        row = anchor.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
        if await row.count() > 0:
            return row

    # Final hardcoded fallback for this test case.
    anchor2 = page.locator("a[href*='/lead-data-scientist']").first
    if await anchor2.count() > 0 and await anchor2.is_visible(timeout=1200):
        row = anchor2.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
        if await row.count() > 0:
            return row

    raise RuntimeError(f"Fant ikke CV med id/tittel/slug: id={SOURCE_RESUME_ID}, title={cv_title}")


async def _open_row_menu(row: Any) -> None:
    buttons = row.locator("button")
    bcount = await buttons.count()
    if bcount == 0:
        raise RuntimeError("Fant ingen knapper i CV-rad")
    for idx in range(bcount - 1, max(-1, bcount - 4), -1):
        btn = buttons.nth(idx)
        if await btn.is_visible(timeout=200):
            await btn.click(timeout=TIMEOUT_MS)
            await asyncio.sleep(0.35)
            return
    raise RuntimeError("Klarte ikke åpne 3-prikk meny")


async def _click_menu_item_by_text(page: Any, pattern: str) -> None:
    item = page.locator(f"[role='menuitem']:has-text('{pattern}'), [role='option']:has-text('{pattern}'), li:has-text('{pattern}'), button:has-text('{pattern}'), a:has-text('{pattern}')").first
    if await item.count() == 0 or not await item.is_visible(timeout=1200):
        raise RuntimeError(f"Fant ikke menyvalg: {pattern}")
    await item.click(timeout=TIMEOUT_MS)
    await asyncio.sleep(0.5)


async def _copy_source_cv(page: Any) -> int | None:
    row = await _find_cv_row(page, SOURCE_CV_TITLE)
    await _open_row_menu(row)
    await _click_menu_item_by_text(page, "Copy")
    await page.wait_for_timeout(500)

    # Fast path: noen Cinode-varianter åpner kopien direkte i editor uten modal.
    fast_url = page.url or ""
    fast_m = re.search(r"/edit/(\d+)", fast_url)
    if fast_m:
        return int(fast_m.group(1))

    # If copy dialog appears: set title + save (scope strictly to active modal).
    title_input = page.locator(
        "app-modal-anchor input[name='title'], "
        "app-modal-anchor input[placeholder*='Title' i], "
        "app-modal-anchor input[placeholder*='CV' i], "
        "[role='dialog'] input[name='title'], "
        "[role='dialog'] input[placeholder*='Title' i], "
        "[role='dialog'] input[placeholder*='CV' i]"
    ).first
    if await title_input.count() > 0 and await title_input.is_visible(timeout=2200):
        await title_input.fill(NEW_CV_TITLE, timeout=TIMEOUT_MS)
        await page.wait_for_timeout(150)

        save_selectors = [
            "app-modal-anchor button:has-text('Save')",
            "app-modal-anchor button:has-text('Create')",
            "app-modal-anchor button:has-text('Copy')",
            "[role='dialog'] button:has-text('Save')",
            "[role='dialog'] button:has-text('Create')",
            "[role='dialog'] button:has-text('Copy')",
            "app-modal-anchor button.button-icon--positive",
            "[role='dialog'] button.button-icon--positive",
        ]
        saved = False
        for selector in save_selectors:
            btn = page.locator(selector).first
            try:
                if await btn.count() == 0 or not await btn.is_visible(timeout=250):
                    continue
                try:
                    await btn.click(timeout=3500)
                except Exception:
                    await btn.click(timeout=3500, force=True)
                await page.wait_for_timeout(700)
                # Success if modal is gone or editor URL opened.
                url = page.url or ""
                if "/edit/" in url:
                    saved = True
                    break
                modal_still_open = await title_input.is_visible(timeout=150)
                if not modal_still_open:
                    saved = True
                    break
            except Exception:
                continue

        if not saved:
            # Final fallback: Enter on title input.
            try:
                await title_input.press("Enter", timeout=1500)
                await page.wait_for_timeout(700)
                saved = True
            except Exception:
                saved = False

        if not saved:
            # Ikke hard-feil dersom vi likevel havnet i editor.
            maybe_url = page.url or ""
            if "/edit/" not in maybe_url:
                raise RuntimeError("Fant ikke fungerende Save/Create i copy-dialog")
    else:
        # Inline confirm fallback.
        await page.keyboard.press("Enter")
        await asyncio.sleep(1.0)

    url = page.url or ""
    m = re.search(r"/edit/(\d+)", url)
    return int(m.group(1)) if m else None


async def _open_editor_by_id(page: Any, resume_id: int, refresh_list: bool = True) -> None:
    # Prefer exact href from list (includes any required slug/path variant).
    if refresh_list:
        await _open_resume_list(page)
    link = page.locator(f"a[href*='/edit/{resume_id}/'], a[href*='/edit/{resume_id}']").first
    target = ""
    if await link.count() > 0 and await link.is_visible(timeout=1200):
        target = (await link.get_attribute("href")) or ""
    if not target:
        # Last fallback: construct minimal URL
        target = f"https://app.cinode.com/xlent/resumes/user/{SOURCE_USER_ID}/edit/{resume_id}"

    if target.startswith("/"):
        target = "https://app.cinode.com" + target
    elif target.startswith("http://") or target.startswith("https://"):
        pass
    else:
        target = "https://app.cinode.com/xlent/" + target.lstrip("/")

    await page.goto(target, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    await page.wait_for_timeout(1200)
    current = page.url or ""
    if "404" in current.lower() or "page not found" in ((await page.locator("body").inner_text()).lower()):
        raise RuntimeError(f"Editor-URL ga 404 for resume_id={resume_id}. URL={current}")
    if f"/edit/{resume_id}" not in current:
        raise RuntimeError(f"Kunne ikke åpne editor for resume_id={resume_id}. URL={current}")


async def _set_title_in_editor(page: Any, new_title: str) -> None:
    selectors = [
        "input[name='title']",
        "input[name*='title' i]",
        "input[id*='title' i]",
        "input[aria-label*='title' i]",
    ]
    for sel in selectors:
        inp = page.locator(sel).first
        try:
            if await inp.count() == 0 or not await inp.is_visible(timeout=300):
                continue
            await inp.fill(new_title, timeout=TIMEOUT_MS)
            try:
                await inp.press("Enter", timeout=1000)
            except Exception:
                pass
            await asyncio.sleep(0.3)
            return
        except Exception:
            continue


async def _open_skills_drawer(page: Any, force_reopen: bool = False) -> Any:
    async def _opened_drawer() -> Any | None:
        drawer = page.locator(
            "mat-sidenav.mat-drawer-end.mat-drawer-opened, "
            "mat-drawer.mat-drawer-end.mat-drawer-opened, "
            ".mat-drawer-end.mat-drawer-opened, "
            "[role='dialog']"
        ).last
        try:
            if await drawer.count() == 0 or not await drawer.is_visible(timeout=120):
                return None
            txt = ((await drawer.inner_text()) or "").lower()
            if "ferdigheter etter kategori" in txt or "skills by category" in txt:
                return drawer
        except Exception:
            return None
        return None

    if force_reopen:
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(220)
        except Exception:
            pass

    # Allerede åpen?
    existing = await _opened_drawer()
    if existing is not None and not force_reopen:
        return existing

    # Vent kort på at seksjonen er rendret.
    for _ in range(25):
        h = page.locator("text=/Ferdigheter etter kategori|Skills by category/i").first
        try:
            if await h.count() > 0 and await h.is_visible(timeout=120):
                break
        except Exception:
            pass
        await page.wait_for_timeout(180)

    # Finn kategori-rad (Produkter/Teknikker) og klikk penn/Edit.
    target = None
    for pattern in [r"^Produkter$", r"^Teknikker$", r"^Products$", r"^Techniques$", r"Produkter", r"Teknikker"]:
        lbl = page.locator(f"text=/{pattern}/i").first
        try:
            if await lbl.count() > 0 and await lbl.is_visible(timeout=180):
                target = lbl
                break
        except Exception:
            continue
    if target is None:
        # Fallback: bruk første synlige penn i "Ferdigheter etter kategori"-seksjonen.
        section_heading = page.locator("text=/Ferdigheter etter kategori|Skills by category/i").first
        section = None
        try:
            if await section_heading.count() > 0:
                section = section_heading.locator("xpath=ancestor::*[self::section or self::div][1]")
        except Exception:
            section = None
        if section is not None:
            try:
                pen_any = section.locator(
                    "button[aria-label*='edit' i], button[title*='edit' i], "
                    "button:has(svg), cui-icon-button button"
                ).first
                if await pen_any.count() > 0 and await pen_any.is_visible(timeout=200):
                    await pen_any.click(timeout=TIMEOUT_MS, force=True)
                    for _ in range(25):
                        await page.wait_for_timeout(180)
                        d = await _opened_drawer()
                        if d is not None:
                            return d
            except Exception:
                pass
        raise RuntimeError("Fant ikke kategori-rad (Produkter/Teknikker)")

    try:
        await target.scroll_into_view_if_needed(timeout=TIMEOUT_MS)
    except Exception:
        pass

    clicked = False
    for up in [1, 2, 3, 4, 5, 6, 7, 8]:
        row = target.locator(f"xpath=ancestor::*[{up}]")
        try:
            if await row.count() == 0 or not await row.is_visible(timeout=120):
                continue
        except Exception:
            continue
        try:
            edit = row.locator("text=/^Edit$/i").first
            if await edit.count() > 0 and await edit.is_visible(timeout=180):
                await edit.click(timeout=TIMEOUT_MS)
                clicked = True
                break
        except Exception:
            pass
        try:
            pen = row.locator(
                "button[aria-label*='edit' i], button[title*='edit' i], "
                "button:has(svg), cui-icon-button button"
            ).first
            if await pen.count() > 0 and await pen.is_visible(timeout=180):
                await pen.click(timeout=TIMEOUT_MS, force=True)
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        raise RuntimeError("Fant ikke penn/Edit for kategori-rad")

    for _ in range(25):
        await page.wait_for_timeout(180)
        d = await _opened_drawer()
        if d is not None:
            return d
    raise RuntimeError("Ferdigheter-drawer åpnet ikke")


async def _add_skill(drawer: Any, page: Any, skill: str) -> None:
    timeout_ms = TIMEOUT_MS

    def _norm(s: str) -> str:
        return " ".join((s or "").strip().lower().split())

    def _norm_skill(s: str) -> str:
        v = (s or "").strip().lower().replace("-", " ").replace("_", " ").replace("/", " ")
        v = re.sub(r"\s+", " ", v).strip()
        return v

    def _row_matches_skill(row_text: str, wanted_skill: str) -> bool:
        row_norm = _norm_skill(row_text)
        wanted_norm = _norm_skill(wanted_skill)
        if not row_norm or not wanted_norm:
            return False
        pattern = r"(?:^|[^a-z0-9])" + re.escape(wanted_norm).replace(r"\ ", r"\s+") + r"(?:$|[^a-z0-9])"
        return re.search(pattern, row_norm) is not None

    async def _click_tab_if_visible(label_pattern: str) -> None:
        name_map = {
            "All|Alle": ["All", "Alle"],
            "All": ["All", "Alle"],
            "Selected": ["Selected", "Valgt", "Valda"],
        }
        names = name_map.get(label_pattern, [label_pattern])
        for name in names:
            for sel in [
                f"button:has-text('{name}')",
                f"[role='tab']:has-text('{name}')",
                f"text=/^{re.escape(name)}\\s*\\(\\d+\\)$/i",
            ]:
                tab = drawer.locator(sel).first
                try:
                    if await tab.count() > 0 and await tab.is_visible(timeout=140):
                        await tab.click(timeout=1400, force=True)
                        await page.wait_for_timeout(170)
                        return
                except Exception:
                    continue

    async def _clear_search() -> None:
        try:
            search_inp = drawer.locator("input[placeholder*='Search' i], input[aria-label*='search' i]").first
            if await search_inp.count() > 0 and await search_inp.is_visible(timeout=120):
                await search_inp.fill("", timeout=timeout_ms)
                await page.wait_for_timeout(120)
        except Exception:
            pass

    async def _click_first_visible(selectors: list[str]) -> Any | None:
        for selector in selectors:
            loc = drawer.locator(selector)
            try:
                count = await loc.count()
            except Exception:
                count = 0
            for i in range(min(count, 12)):
                candidate = loc.nth(i)
                try:
                    if not await candidate.is_visible(timeout=140):
                        continue
                    try:
                        await candidate.click(timeout=1600)
                    except Exception:
                        await candidate.click(timeout=1600, force=True)
                    await page.wait_for_timeout(160)
                    return candidate
                except Exception:
                    continue
        return None

    async def _find_add_skill_input() -> Any | None:
        inputs = drawer.locator("input")
        try:
            count = await inputs.count()
        except Exception:
            count = 0
        for i in range(min(count, 20)):
            inp = inputs.nth(i)
            try:
                if not await inp.is_visible(timeout=120):
                    continue
                ph = ((await inp.get_attribute("placeholder")) or "").strip().lower()
                ar = ((await inp.get_attribute("aria-label")) or "").strip().lower()
                tags = f"{ph} {ar}"
                if "search" in tags:
                    continue
                if "e.g. roles" in tags or "select category" in tags:
                    continue
                if "try typing" in tags or "project management" in tags or "add skill" in tags:
                    return inp
            except Exception:
                continue
        return None

    async def _refresh_drawer_ref() -> Any:
        updated = await _open_skills_drawer(page, force_reopen=False)
        return updated if updated is not None else drawer

    drawer = await _refresh_drawer_ref()

    async def _close_level_popup_if_open() -> None:
        try:
            level_popup = drawer.locator("text=/Level settings|Nivåinnstillinger/i").first
            if await level_popup.count() > 0 and await level_popup.is_visible(timeout=120):
                close_btn = level_popup.locator("xpath=ancestor::div[1]").locator(
                    "button[aria-label*='close' i], button[title*='close' i], button:has-text('×')"
                ).first
                if await close_btn.count() > 0 and await close_btn.is_visible(timeout=120):
                    await close_btn.click(timeout=1200, force=True)
                    await page.wait_for_timeout(160)
                else:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(160)
        except Exception:
            pass

    async def _set_level_five(container: Any) -> None:
        try:
            direct_five = container.locator("button:has-text('5'), [role='button']:has-text('5')").first
            if await direct_five.count() > 0 and await direct_five.is_visible(timeout=150):
                await direct_five.click(timeout=min(timeout_ms, 1800))
                await page.wait_for_timeout(120)
                return
        except Exception:
            pass

    await _close_level_popup_if_open()

    existing = drawer.locator(f"text=/{re.escape(skill)}/i").first
    try:
        if await existing.count() > 0 and await existing.is_visible(timeout=120):
            row = existing.locator("xpath=ancestor::*[self::li or self::tr or self::div][1]")
            cb = row.locator("input[type='checkbox'], [role='checkbox']").first
            if await cb.count() > 0:
                role = ((await cb.get_attribute("role")) or "").strip().lower()
                if role == "checkbox":
                    state = ((await cb.get_attribute("aria-checked")) or "").strip().lower()
                    if state not in {"true", "1"}:
                        await cb.click(timeout=timeout_ms)
                else:
                    try:
                        checked = await cb.is_checked()
                    except Exception:
                        checked = False
                    if not checked:
                        await cb.check(timeout=timeout_ms)
                await _set_level_five(row)
                return
    except Exception:
        pass

    async def _ensure_category_for_skills() -> None:
        # Hvis '+' allerede er synlig er kategori klar.
        plus_probe_selectors = [
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'skills to include') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'ferdigheter som skal inkluderes')]/following::button[contains(@class,'positive') or contains(@class,'button-icon')][1]",
            "button[aria-label*='add' i]",
            "button[title*='add' i]",
            "button.button-icon--positive",
            "cui-button-icon button",
        ]
        plus_probe = None
        for sel in plus_probe_selectors:
            cand = drawer.locator(sel).first
            try:
                if await cand.count() > 0 and await cand.is_visible(timeout=120):
                    plus_probe = cand
                    break
            except Exception:
                continue
        try:
            if plus_probe is not None and await plus_probe.count() > 0 and await plus_probe.is_visible(timeout=200):
                return
        except Exception:
            pass

        cat_input = drawer.locator(
            "input[placeholder*='e.g. Roles' i], "
            "input[placeholder*='Select category' i], "
            "input[placeholder*='Kategori' i]"
        ).first
        if await cat_input.count() > 0 and await cat_input.is_visible(timeout=600):
            try:
                await cat_input.click(timeout=1200)
            except Exception:
                pass
            # Prøv å velge kjent kategori direkte.
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
                    await opt.click(timeout=1200, force=True)
                    picked = True
                    break
                except Exception:
                    continue
            if not picked:
                try:
                    await cat_input.fill("", timeout=1200)
                    await cat_input.type("Teknikker", delay=15, timeout=1400)
                    await cat_input.press("Enter", timeout=1200)
                except Exception:
                    pass

        for _ in range(18):
            try:
                if plus_probe is not None and await plus_probe.count() > 0 and await plus_probe.is_visible(timeout=120):
                    return
            except Exception:
                pass
            await page.wait_for_timeout(180)

        # Recovery: reopen drawer in clean state once.
        fresh = await _open_skills_drawer(page, force_reopen=True)
        if fresh is not None:
            nonlocal_drawer[0] = fresh
            await page.wait_for_timeout(220)

    # nonlocal holder for drawer refresh from nested helper
    nonlocal_drawer = [drawer]

    await _ensure_category_for_skills()
    drawer = nonlocal_drawer[0]
    drawer = await _refresh_drawer_ref()
    await _click_tab_if_visible("All|Alle")
    await _clear_search()

    plus_selectors = [
        "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'skills to include')]/following::button[contains(@class,'positive') or contains(@class,'button-icon')][1]",
        "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'ferdigheter som skal inkluderes')]/following::button[contains(@class,'positive') or contains(@class,'button-icon')][1]",
        "button[aria-label*='add' i]",
        "button[title*='add' i]",
        "button.button-icon--positive",
    ]
    clicked_plus = False
    plus_button = None
    await _close_level_popup_if_open()
    clicked = await _click_first_visible(plus_selectors)
    if clicked is not None:
        plus_button = clicked
        clicked_plus = True
    if not clicked_plus:
        raise RuntimeError("Fant ikke grønn + ved 'Skills to include'")

    typed = False
    typed_input = None
    for _ in range(4):
        inp = await _find_add_skill_input()
        try:
            if inp is not None and await inp.is_visible(timeout=350):
                await inp.fill("", timeout=timeout_ms)
                await inp.type(skill, delay=20, timeout=timeout_ms)
                try:
                    v = await inp.input_value()
                    _log(f"DEBUG add-skill input etter type '{skill}': '{v}'")
                except Exception:
                    pass
                typed = True
                typed_input = inp
                break
        except Exception:
            pass
        try:
            if plus_button is not None and await plus_button.count() > 0 and await plus_button.is_visible(timeout=150):
                await plus_button.click(timeout=1200, force=True)
                await page.wait_for_timeout(220)
        except Exception:
            pass
    if not typed:
        try:
            visible_inputs = drawer.locator("input")
            icount = await visible_inputs.count()
            previews: list[str] = []
            for i in range(min(icount, 6)):
                ii = visible_inputs.nth(i)
                if not await ii.is_visible(timeout=80):
                    continue
                ph = ((await ii.get_attribute("placeholder")) or "").strip()
                al = ((await ii.get_attribute("aria-label")) or "").strip()
                previews.append(f"placeholder='{ph}' aria='{al}'")
            if previews:
                _log("DEBUG synlige inputfelt: " + " | ".join(previews))
        except Exception:
            pass
        raise RuntimeError("Fant ikke 'Add skill' inputfelt")

    add_clicked = False

    async def _click_button_right_of_add_input(inp: Any) -> bool:
        try:
            inp_box = await inp.bounding_box()
        except Exception:
            inp_box = None
        if not inp_box:
            return False

        buttons = drawer.locator("button")
        try:
            bcount = await buttons.count()
        except Exception:
            bcount = 0
        chosen = None
        chosen_x = None
        for i in range(min(bcount, 80)):
            b = buttons.nth(i)
            try:
                if not await b.is_visible(timeout=60):
                    continue
                box = await b.bounding_box()
                if not box:
                    continue
                same_row = abs((box["y"] + box["height"] / 2) - (inp_box["y"] + inp_box["height"] / 2)) <= max(
                    14, inp_box["height"] * 0.9
                )
                is_right = box["x"] >= inp_box["x"] + inp_box["width"] - 6
                if not same_row or not is_right:
                    continue
                if chosen is None or box["x"] < (chosen_x or 1e9):
                    chosen = b
                    chosen_x = box["x"]
            except Exception:
                continue

        if chosen is None:
            return False
        try:
            await chosen.click(timeout=min(timeout_ms, 1800), force=True)
            await page.wait_for_timeout(320)
            return True
        except Exception:
            return False

    if typed_input is not None:
        add_clicked = await _click_button_right_of_add_input(typed_input)
        if add_clicked:
            _log(f"DEBUG klikket søkeknapp ved Add skill for '{skill}'")
        if not add_clicked:
            try:
                box = await typed_input.bounding_box()
                if box:
                    click_x = box["x"] + box["width"] - 8
                    click_y = box["y"] + (box["height"] / 2)
                    await page.mouse.click(click_x, click_y)
                    await page.wait_for_timeout(320)
                    add_clicked = True
                    _log(f"DEBUG klikket høyrekant i Add skill input for '{skill}'")
            except Exception:
                pass

    try:
        if typed_input is not None:
            await typed_input.press("Enter", timeout=1200)
            await page.wait_for_timeout(220)
    except Exception:
        pass
    exact_option_selectors = [
        f"text=/^Add\\s+{re.escape(skill)}$/i",
        f"text=/^Create\\s+{re.escape(skill)}$/i",
        f"text=/^Legg\\s+til\\s+{re.escape(skill)}$/i",
        f"text=/^{re.escape(skill)}$/i",
    ]
    # Wait for async option loading to complete, then pick exact option only.
    option_deadline = asyncio.get_running_loop().time() + 8.0
    while asyncio.get_running_loop().time() < option_deadline and not add_clicked:
        for selector in exact_option_selectors:
            cand = page.locator(selector).first
            try:
                if await cand.count() == 0 or not await cand.is_visible(timeout=120):
                    continue
                await cand.click(timeout=min(timeout_ms, 1800), force=True)
                await page.wait_for_timeout(260)
                add_clicked = True
                break
            except Exception:
                continue
        if add_clicked:
            break
        await page.wait_for_timeout(220)

    if not add_clicked:
        # Newer Cinode UI: explicit search icon at right side of "Add skill" field.
        add_search_selectors = [
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'add skill')]/following::input[1]/following::button[1]",
            "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'legg til ferdighet')]/following::input[1]/following::button[1]",
        ]
        for sel in add_search_selectors:
            btn = drawer.locator(sel).first
            try:
                if await btn.count() == 0 or not await btn.is_visible(timeout=140):
                    continue
                await btn.click(timeout=min(timeout_ms, 1800), force=True)
                await page.wait_for_timeout(350)
                add_clicked = True
                break
            except Exception:
                continue

    if not add_clicked:
        raise RuntimeError(f"Fant ikke eksakt Add/Create-valg eller søkeknapp for skill: {skill}")

    await page.wait_for_timeout(500)

    try:
        search_inp = drawer.locator("input[placeholder*='Search' i]").first
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=120):
            cur = await search_inp.input_value()
            if cur.strip():
                await search_inp.fill("", timeout=timeout_ms)
                await page.wait_for_timeout(150)
    except Exception:
        pass

    await _click_tab_if_visible("All|Alle")

    try:
        search_inp = drawer.locator("input[placeholder*='Search' i]").first
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=150):
            await search_inp.fill("", timeout=timeout_ms)
            await search_inp.type(skill, delay=15, timeout=timeout_ms)
            await page.wait_for_timeout(220)
    except Exception:
        pass

    async def _find_skill_container() -> Any | None:
        matches = drawer.locator(f"text=/{re.escape(skill)}/i")
        try:
            mcount = await matches.count()
        except Exception:
            mcount = 0
        for i in range(min(mcount, 25)):
            m = matches.nth(i)
            try:
                if not await m.is_visible(timeout=120):
                    continue
            except Exception:
                continue
            for up in [1, 2, 3, 4, 5, 6, 7]:
                c = m.locator(f"xpath=ancestor::*[{up}]")
                try:
                    if await c.count() == 0:
                        continue
                    txt = " ".join((((await c.inner_text()) or "").strip()).split())
                    low = txt.lower()
                    if "add skill" in low or "skills to include" in low:
                        continue
                    if not _row_matches_skill(txt, skill):
                        continue
                    ctrl = c.locator(
                        "input[type='checkbox'], [role='checkbox'], button:has(svg use[href*='check']), button[class*='positive'], button[aria-label*='select' i], button[title*='select' i], button[aria-label*='include' i], button[title*='include' i], button[class*='toggle'], button[class*='check']"
                    )
                    text_inputs = await c.locator("input[type='text']").count()
                    if await ctrl.count() > 0 and text_inputs == 0:
                        return c
                except Exception:
                    continue
        return None

    container = None
    for _ in range(3):
        container = await _find_skill_container()
        if container is not None:
            try:
                _log(f"DEBUG match-rad for {skill}: " + " ".join((((await container.inner_text()) or "").strip()).split())[:220])
            except Exception:
                pass
            break
        if typed_input is not None:
            try:
                await typed_input.press("Enter", timeout=1200)
            except Exception:
                pass
        await page.wait_for_timeout(300)

    if container is None:
        # Fallback: skill kan være lagt til/aktiv i "Selected"-fanen uten synlig rad i nåværende filter.
        try:
            search_inp = drawer.locator("input[placeholder*='Search' i]").first
            if await search_inp.count() > 0 and await search_inp.is_visible(timeout=120):
                await search_inp.fill("", timeout=timeout_ms)
                await page.wait_for_timeout(150)
        except Exception:
            pass
        await _click_tab_if_visible("Selected")
        selected_rows = drawer.locator("li, tr, div")
        try:
            scount = await selected_rows.count()
        except Exception:
            scount = 0
        for i in range(min(scount, 120)):
            row = selected_rows.nth(i)
            try:
                if not await row.is_visible(timeout=100):
                    continue
                txt = ((await row.inner_text()) or "").strip()
                low = txt.lower()
                if not txt:
                    continue
                if "select category" in low or "skills to include" in low or "add skill" in low:
                    continue
                ctrls = row.locator(
                    "input[type='checkbox'], [role='checkbox'], button:has(svg use[href*='check']), button[class*='positive'], button[class*='toggle'], button[class*='check']"
                )
                if await ctrls.count() == 0:
                    continue
                if await row.locator("input[type='text']").count() > 0:
                    continue
                if _row_matches_skill(txt, skill):
                    _log(f"DEBUG fallback Selected-hit for {skill}: " + " ".join(txt.split())[:220])
                    return
            except Exception:
                continue
        raise RuntimeError(f"Fant ikke skill-rad etter add: {skill}")

    cb = container.locator("input[type='checkbox'], [role='checkbox']").first
    if await cb.count() > 0:
        role = ((await cb.get_attribute("role")) or "").strip().lower()
        if role == "checkbox":
            state = ((await cb.get_attribute("aria-checked")) or "").strip().lower()
            if state not in {"true", "1"}:
                await cb.click(timeout=timeout_ms)
                _log(f"DEBUG checket checkbox for {skill} via aria-checked")
        else:
            try:
                checked = await cb.is_checked()
            except Exception:
                checked = False
            if not checked:
                await cb.check(timeout=timeout_ms)
                _log(f"DEBUG checket checkbox for {skill} via is_checked")
        await _set_level_five(container)
        return

    toggle = container.locator(
        "button:has(svg use[href*='check']), button[class*='positive'], button[aria-label*='select' i], button[title*='select' i], button[aria-label*='include' i], button[title*='include' i]"
    ).last
    if await toggle.count() == 0:
        raise RuntimeError(f"Fant ikke checkbox/toggle for skill: {skill}")
    selected = False
    try:
        aria_pressed = ((await toggle.get_attribute("aria-pressed")) or "").strip().lower()
        cls = ((await toggle.get_attribute("class")) or "").strip().lower()
        if aria_pressed in {"true", "1"} or "positive" in cls or "selected" in cls or "active" in cls:
            selected = True
    except Exception:
        pass
    if not selected:
        await toggle.click(timeout=timeout_ms, force=True)
        _log(f"DEBUG klikket toggle for {skill}")
    await _set_level_five(container)


async def _save_skills_drawer(drawer: Any, page: Any) -> None:
    selectors = [
        "button:has-text('Save')",
        "button:has-text('Lagre')",
        "a:has-text('Save')",
        "a:has-text('Lagre')",
        "[role='button']:has-text('Save')",
        "[role='button']:has-text('Lagre')",
    ]
    for selector in selectors:
        btn = drawer.locator(selector).first
        try:
            if await btn.count() == 0 or not await btn.is_visible(timeout=200):
                continue
            try:
                await btn.click(timeout=TIMEOUT_MS)
            except Exception:
                await btn.click(timeout=TIMEOUT_MS, force=True)
            await page.wait_for_timeout(700)
            return
        except Exception:
            continue
    raise RuntimeError("Fant ikke Save/Lagre i ferdighets-drawer")


async def _verify_selected_skills(drawer: Any, page: Any, skills: list[str]) -> tuple[bool, list[str], list[str]]:
    def _norm_skill(s: str) -> str:
        v = (s or "").strip().lower().replace("-", " ").replace("_", " ").replace("/", " ")
        v = re.sub(r"\s+", " ", v).strip()
        return v

    def _row_matches_skill(row_text: str, wanted_skill: str) -> bool:
        row_norm = _norm_skill(row_text)
        wanted_norm = _norm_skill(wanted_skill)
        if not row_norm or not wanted_norm:
            return False
        pattern = r"(?:^|[^a-z0-9])" + re.escape(wanted_norm).replace(r"\ ", r"\s+") + r"(?:$|[^a-z0-9])"
        return re.search(pattern, row_norm) is not None

    for name in ["Selected", "Valgt", "Valda"]:
        clicked = False
        for sel in [
            f"button:has-text('{name}')",
            f"[role='tab']:has-text('{name}')",
            f"text=/^{re.escape(name)}\\s*\\(\\d+\\)$/i",
        ]:
            tab = drawer.locator(sel).first
            try:
                if await tab.count() == 0 or not await tab.is_visible(timeout=150):
                    continue
                await tab.click(timeout=1500, force=True)
                await page.wait_for_timeout(250)
                clicked = True
                break
            except Exception:
                continue
        if clicked:
            break

    rows = drawer.locator("li, tr, div")
    samples: list[str] = []
    try:
        rcount = await rows.count()
    except Exception:
        rcount = 0
    for i in range(min(rcount, 180)):
        row = rows.nth(i)
        try:
            if not await row.is_visible(timeout=60):
                continue
            txt = " ".join((((await row.inner_text()) or "").strip()).split())
            low = txt.lower()
            if not txt:
                continue
            if "skills to include" in low or "add skill" in low or "search" == low:
                continue
            ctrls = row.locator(
                "input[type='checkbox'], [role='checkbox'], button:has(svg use[href*='check']), button[class*='positive'], button[class*='toggle'], button[class*='check']"
            )
            if await ctrls.count() == 0:
                continue
            if await row.locator("input[type='text']").count() > 0:
                continue
            if len(samples) < 25:
                samples.append(txt)
        except Exception:
            continue

    missing: list[str] = []
    for skill in skills:
        found = False
        for txt in samples:
            if _row_matches_skill(txt, skill):
                found = True
                break
        if not found:
            # Broaden scan if not found in samples.
            for i in range(min(rcount, 220)):
                row = rows.nth(i)
                try:
                    if not await row.is_visible(timeout=60):
                        continue
                    txt = " ".join((((await row.inner_text()) or "").strip()).split())
                    low = txt.lower()
                    if "skills to include" in low or "add skill" in low or "search" == low:
                        continue
                    ctrls = row.locator(
                        "input[type='checkbox'], [role='checkbox'], button:has(svg use[href*='check']), button[class*='positive'], button[class*='toggle'], button[class*='check']"
                    )
                    if await ctrls.count() == 0:
                        continue
                    if await row.locator("input[type='text']").count() > 0:
                        continue
                    if _row_matches_skill(txt, skill):
                        found = True
                        break
                except Exception:
                    continue
        if not found:
            missing.append(skill)

    return (len(missing) == 0, missing, samples)


async def run() -> None:
    run_dir = _repo_root() / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = run_dir / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    shot_path = run_dir / "skill-simple-failure.png"

    _log(f"Start: {START_URL}")
    _log(f"Bruker (hardkodet): {SOURCE_USER_ID} (Thomas Elboth)")
    _log(f"Kilde-CV: {SOURCE_CV_TITLE}")
    _log(f"Ny CV: {NEW_CV_TITLE}")
    _log(f"Skills: {', '.join(SKILLS)}")
    _log("Sikrer at skills finnes i Cinode-profil via API ...")
    _ensure_skills_exist_via_api(SKILLS)
    _log("API pre-create ferdig")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=HEADLESS,
            ignore_https_errors=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await _open_resume_list(page)
            before_ids = await _collect_resume_ids(page)
            _log(f"CV-er før copy: {len(before_ids)}")
            copied_id = await _copy_source_cv(page)
            _log(f"Kopiert CV (resume_id={copied_id or 'ukjent'})")
            target_id = copied_id
            after_ids: set[int] = set()
            if not target_id:
                await _open_resume_list(page)
                after_ids = await _collect_resume_ids(page)
                new_ids = sorted([rid for rid in after_ids if rid not in before_ids], reverse=True)
                target_id = new_ids[0] if new_ids else None
            if not target_id:
                _log("Ingen ny resume_id etter copy (mulig CV-grense nådd). Bruker eksisterende copy-rad som fallback.")
                if not after_ids:
                    await _open_resume_list(page)
                    after_ids = await _collect_resume_ids(page)
                candidates = sorted([rid for rid in after_ids if rid != SOURCE_RESUME_ID], reverse=True)
                if not candidates:
                    _log("ADVARSEL: Fant ingen kopiert CV. Faller tilbake til kilde-CV for å fullføre skill-test.")
                    candidates = [SOURCE_RESUME_ID]
                target_id = int(candidates[0])
                _log(f"Fallback CV id: {target_id}")
                await _open_editor_by_id(page, int(target_id), refresh_list=False)
                _log(f"Åpnet Edit for fallback CV (resume_id={target_id})")
            else:
                _log(f"Ny CV id: {target_id}")
                current_url = page.url or ""
                if f"/edit/{int(target_id)}" not in current_url:
                    await _open_editor_by_id(page, int(target_id), refresh_list=False)
                _log(f"Åpnet Edit for ny CV (resume_id={target_id})")

            await _set_title_in_editor(page, NEW_CV_TITLE)

            for skill in SKILLS:
                drawer = await _open_skills_drawer(page)
                _log("Åpnet kategori-dialog for ferdigheter")
                await _add_skill(drawer, page, skill)
                _log(f"La til skill: {skill}")

            drawer = await _open_skills_drawer(page)
            await _save_skills_drawer(drawer, page)
            _log("Lagret ferdigheter (Save)")
            drawer = await _open_skills_drawer(page)
            ok_selected, missing, samples = await _verify_selected_skills(drawer, page, SKILLS)
            if samples:
                _log("DEBUG Selected skills (utdrag):")
                for s in samples[:12]:
                    _log(f"  - {s}")
            if not ok_selected:
                raise RuntimeError(f"Verifikasjon feilet. Mangler skills: {', '.join(missing)}")

            _log("OK: Ferdig")
        except Exception as exc:
            _log(f"FEIL: {exc}")
            try:
                await page.screenshot(path=str(shot_path), full_page=True)
                _log(f"Skjermbilde: {shot_path}")
            except Exception:
                pass
            raise
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(run())
