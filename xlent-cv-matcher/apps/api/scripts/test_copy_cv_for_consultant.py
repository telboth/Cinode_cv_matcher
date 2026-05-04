from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

START_URL = "https://app.cinode.com/xlent/resumes/all"
DEFAULT_QUERY = "Anne"
DEFAULT_NAME = "Anne Rønning"
DEFAULT_CONSULTANT_ID = "118976"
DEFAULT_SOURCE_RESUME_ID = 445334
DEFAULT_HEADLESS = False
DEFAULT_TIMEOUT_MS = 30000


@dataclass
class Config:
    query: str
    consultant_name: str
    consultant_id: str
    source_resume_id: int
    new_title: str
    headless: bool
    timeout_ms: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _log(message: str) -> None:
    print(message, flush=True)


def _extract_resume_id(url: str) -> int | None:
    m = re.search(r"/edit/(\d+)", url or "", flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


async def _goto_resume_all(page: Any, timeout_ms: int) -> None:
    await page.goto(START_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(900)
    if any(token in (page.url or "").lower() for token in ["login", "signin", "returnurl="]):
        raise RuntimeError("Ikke innlogget i Cinode-profilen. Logg inn i vanlig browser først.")


async def _find_top_search_input(page: Any) -> Any:
    candidates = page.locator(
        "input[type='search'], input[placeholder*='Search' i], input[aria-label*='Search' i], input[placeholder='Search']"
    )
    count = await candidates.count()
    for i in range(min(count, 20)):
        inp = candidates.nth(i)
        try:
            if not await inp.is_visible(timeout=120):
                continue
            box = await inp.bounding_box()
            if not box:
                continue
            # Top navigation search in Cinode usually sits near the top bar.
            if box["y"] <= 140 and box["width"] >= 160:
                return inp
        except Exception:
            continue
    raise RuntimeError("Fant ikke toppsøk-feltet i Cinode")


async def _select_consultant_from_top_search(page: Any, *, query: str, consultant_name: str, timeout_ms: int) -> None:
    top_search = await _find_top_search_input(page)
    await top_search.click(timeout=timeout_ms)
    await top_search.fill("")
    await top_search.type(query, delay=30, timeout=timeout_ms)
    await page.wait_for_timeout(650)

    option_selectors = [
        f"[role='option']:has-text('{consultant_name}')",
        f"[role='menuitem']:has-text('{consultant_name}')",
        f"li:has-text('{consultant_name}')",
        f"div:has-text('{consultant_name}')",
    ]
    clicked = False
    for selector in option_selectors:
        item = page.locator(selector).first
        try:
            if await item.count() == 0 or not await item.is_visible(timeout=180):
                continue
            await item.click(timeout=min(timeout_ms, 5000), force=True)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        # Fallback: open full results, then click consultant.
        all_results = page.locator("text=/Show all results for/i, text=/Vis alle resultater for/i").first
        if await all_results.count() > 0 and await all_results.is_visible(timeout=400):
            await all_results.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(700)
            person = page.locator(f"text=/{re.escape(consultant_name)}/i").first
            if await person.count() > 0 and await person.is_visible(timeout=1000):
                await person.click(timeout=min(timeout_ms, 5000), force=True)
                clicked = True
    if not clicked:
        raise RuntimeError(f"Fant ikke konsulent i toppsøk: {consultant_name}")

    await page.wait_for_timeout(900)
    url = page.url or ""
    if "/organisation/employees/" not in url.lower():
        # Some flows stay on same page briefly; verify heading.
        heading = page.locator(f"text=/{re.escape(consultant_name)}/i").first
        if await heading.count() == 0:
            raise RuntimeError(
                f"Konsulentvalg ser ikke ut til å ha åpnet ansattsiden. URL={url}"
            )


async def _open_cvs_tab(page: Any, timeout_ms: int) -> None:
    selectors = [
        "[role='tab']:has-text('CVs')",
        "button[role='tab']:has-text('CVs')",
        "a[role='tab']:has-text('CVs')",
        "text=/^\\s*CVs\\s*$/i",
    ]
    for selector in selectors:
        tab = page.locator(selector).first
        try:
            if await tab.count() == 0 or not await tab.is_visible(timeout=120):
                continue
            box = await tab.bounding_box()
            if not box:
                continue
            # Employee tabs sit in the content area, not the left navigation.
            if box["x"] < 220 or box["y"] > 520:
                continue
            await tab.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(700)
            return
        except Exception:
            continue
    # It's acceptable if CV table is already visible without clicking tab.
    if "/organisation/employees/" not in (page.url or "").lower():
        raise RuntimeError("Ikke på ansattside etter CVs-fokus")
    cv_heading = page.locator("text=/^\\s*CV\\s*$/i").first
    if await cv_heading.count() == 0:
        raise RuntimeError("Fant ikke 'CV'-seksjon på ansattsiden")


async def _collect_resume_ids(page: Any) -> set[int]:
    ids: set[int] = set()
    anchors = page.locator("a[href*='/resumes/user/'][href*='/edit/']")
    count = await anchors.count()
    for i in range(min(count, 300)):
        a = anchors.nth(i)
        try:
            href = (await a.get_attribute("href")) or ""
            rid = _extract_resume_id(href)
            if rid:
                ids.add(rid)
        except Exception:
            continue
    return ids


async def _find_resume_row(page: Any, source_resume_id: int) -> Any:
    for _ in range(8):
        anchor = page.locator(f"a[href*='/edit/{source_resume_id}/'], a[href*='/edit/{source_resume_id}']").first
        if await anchor.count() > 0 and await anchor.is_visible(timeout=450):
            row = anchor.locator("xpath=ancestor::tr[1]")
            if await row.count() > 0:
                return row
            row2 = anchor.locator("xpath=ancestor::*[self::li or self::div][1]")
            if await row2.count() > 0:
                return row2
        await page.mouse.wheel(0, 1300)
        await page.wait_for_timeout(220)

    # Fallback for employee CV list where rows are plain text (no edit links in title cell).
    table_rows = page.locator("table tbody tr")
    tcount = await table_rows.count()
    for i in range(min(tcount, 12)):
        tr = table_rows.nth(i)
        try:
            if not await tr.is_visible(timeout=120):
                continue
            text = ((await tr.inner_text()) or "").strip().lower()
            if not text or "title" in text and "last activity" in text:
                continue
            if "share" not in text and "created" not in text:
                continue
            return tr
        except Exception:
            continue

    # Fallback for list layouts rendered as divs (common in Cinode employee pages).
    rows = page.locator("tr, [role='row'], div")
    rcount = await rows.count()
    for i in range(min(rcount, 900)):
        row = rows.nth(i)
        try:
            if not await row.is_visible(timeout=60):
                continue
            text = re.sub(r"\s+", " ", ((await row.inner_text()) or "").strip().lower())
            if not text or len(text) > 380:
                continue
            if ("by anne rønning" not in text) and ("by anne ronning" not in text):
                continue
            if "xlent" not in text:
                continue
            buttons = row.locator("button")
            if await buttons.count() < 2:
                continue
            return row
        except Exception:
            continue

    raise RuntimeError(f"Fant ikke CV-rad for resume_id={source_resume_id}")


async def _click_copy_menu_item(page: Any, timeout_ms: int) -> bool:
    selectors = [
        "button:has-text('Copy')",
        "a:has-text('Copy')",
        "button:has-text('Duplicate')",
        "a:has-text('Duplicate')",
        "button:has-text('Kopiera')",
        "a:has-text('Kopiera')",
        "button:has-text('Kopier')",
        "a:has-text('Kopier')",
        "button:has-text('Kopi')",
        "a:has-text('Kopi')",
    ]
    for selector in selectors:
        item = page.locator(selector).first
        try:
            if await item.count() == 0 or not await item.is_visible(timeout=180):
                continue
            await item.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(500)
            return True
        except Exception:
            continue

    # Generic text fallback for custom dropdown components without semantic roles.
    generic = page.locator("text=/\\b(Copy|Duplicate|Kopiera|Kopier|Kopi|Dupl)\\b/i").first
    try:
        if await generic.count() > 0 and await generic.is_visible(timeout=250):
            await generic.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(500)
            return True
    except Exception:
        pass

    # Fallback: inspect visible menu items and pick the one that looks like copy/duplicate.
    menu_items = page.locator("[role='menuitem'], [role='option'], li[role='option'], button[role='menuitem']")
    count = await menu_items.count()
    for i in range(min(count, 20)):
        it = menu_items.nth(i)
        try:
            if not await it.is_visible(timeout=100):
                continue
            text = ((await it.inner_text()) or "").strip().lower()
            if any(token in text for token in ["copy", "duplicate", "kopi", "kopier", "kopiera", "dupl"]):
                await it.click(timeout=min(timeout_ms, 5000), force=True)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            continue

    # Debug preview
    previews: list[str] = []
    for i in range(min(count, 10)):
        it = menu_items.nth(i)
        try:
            if not await it.is_visible(timeout=60):
                continue
            t = ((await it.inner_text()) or "").strip()
            if t:
                previews.append(re.sub(r"\s+", " ", t)[:80])
        except Exception:
            continue
    _log(f"DEBUG: fant ikke copy i meny. Menyinnhold: {previews}")
    return False


async def _close_share_dialog_if_open(page: Any, timeout_ms: int) -> bool:
    share = page.locator("text=/^\\s*Share CV\\s*$/i").first
    try:
        if await share.count() == 0 or not await share.is_visible(timeout=120):
            return False
    except Exception:
        return False
    for selector in [
        "app-modal-anchor button[aria-label*='close' i]",
        "button:has-text('Close')",
        "button:has-text('Lukk')",
        "button[aria-label*='close' i]",
        "button[title*='close' i]",
    ]:
        btn = page.locator(selector).first
        try:
            if await btn.count() == 0 or not await btn.is_visible(timeout=120):
                continue
            await btn.click(timeout=min(timeout_ms, 2000), force=True)
            await page.wait_for_timeout(250)
            return True
        except Exception:
            continue
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
    except Exception:
        pass
    return True


async def _click_copy_from_row_buttons(row: Any, page: Any, timeout_ms: int) -> None:
    buttons = row.locator("button")
    bcount = await buttons.count()
    if bcount == 0:
        raise RuntimeError("Fant ingen knapper i valgt CV-rad")

    # Try right-most buttons first (usually export icon + kebab menu).
    tried = 0
    for idx in range(bcount - 1, max(-1, bcount - 6), -1):
        btn = buttons.nth(idx)
        try:
            if not await btn.is_visible(timeout=120):
                continue
            tried += 1
            try:
                txt = re.sub(r"\s+", " ", ((await btn.inner_text()) or "").strip())
            except Exception:
                txt = ""
            aria = ((await btn.get_attribute("aria-label")) or "").strip()
            title = ((await btn.get_attribute("title")) or "").strip()
            _log(f"DEBUG: prøver radknapp idx={idx} text='{txt}' aria='{aria}' title='{title}'")
            await btn.click(timeout=min(timeout_ms, 3000), force=True)
            await asyncio.sleep(0.35)
            copied = await _click_copy_menu_item(page, timeout_ms)
            if copied:
                return
            if await _close_share_dialog_if_open(page, timeout_ms):
                _log("DEBUG: lukket Share CV-dialog, prøver neste radknapp")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        except Exception:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            continue
    raise RuntimeError(f"Fant ikke Copy/Duplicate i radhandlingene (prøvde {tried} knapper)")


async def _confirm_copy_dialog(page: Any, *, new_title: str, timeout_ms: int) -> None:
    dialogs = page.get_by_role("dialog")
    try:
        if await dialogs.count() == 0:
            return
        dlg = dialogs.last
        if not await dlg.is_visible(timeout=180):
            return
    except Exception:
        return

    title_inputs = dlg.locator(
        "input[name='title'], input[placeholder*='Title' i], input[placeholder*='CV' i], input[type='text']"
    )
    try:
        if await title_inputs.count() > 0:
            title_input = title_inputs.first
            if await title_input.is_visible(timeout=180):
                await title_input.fill("")
                await title_input.type(new_title, delay=20, timeout=min(timeout_ms, 5000))
    except Exception:
        pass

    save_selectors = [
        "button:has-text('Save')",
        "button:has-text('Create')",
        "button:has-text('Copy')",
        "button:has-text('Lagre')",
        "button:has-text('Opprett')",
        "button:has-text('Skapa')",
    ]
    for selector in save_selectors:
        btn = dlg.locator(selector).first
        try:
            if await btn.count() == 0 or not await btn.is_visible(timeout=180):
                continue
            await btn.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(900)
            return
        except Exception:
            continue


async def _wait_for_new_resume(page: Any, *, before_ids: set[int], source_resume_id: int, timeout_ms: int) -> int | None:
    deadline = asyncio.get_running_loop().time() + min(30.0, timeout_ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        current_id = _extract_resume_id(page.url or "")
        if current_id and current_id != source_resume_id and current_id not in before_ids:
            return current_id
        try:
            ids_now = await _collect_resume_ids(page)
            new_ids = [rid for rid in ids_now if rid not in before_ids and rid != source_resume_id]
            if new_ids:
                return max(new_ids)
        except Exception:
            pass
        await page.wait_for_timeout(500)
    return None


def _build_config() -> Config:
    parser = argparse.ArgumentParser(description="Test: velg konsulent via toppsøk og kopier CV til dummy.")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--consultant-id", default=DEFAULT_CONSULTANT_ID)
    parser.add_argument("--resume-id", type=int, default=DEFAULT_SOURCE_RESUME_ID)
    parser.add_argument("--title", default="")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    args = parser.parse_args()

    title = args.title.strip() or f"dummy_cv_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return Config(
        query=str(args.query),
        consultant_name=str(args.name),
        consultant_id=str(args.consultant_id),
        source_resume_id=int(args.resume_id),
        new_title=title,
        headless=bool(args.headless) if args.headless else DEFAULT_HEADLESS,
        timeout_ms=max(10000, int(args.timeout_ms)),
    )


async def run(config: Config) -> None:
    run_dir = _repo_root() / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = run_dir / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    shot_path = run_dir / "copy-cv-consultant-failure.png"

    _log(f"Start: {START_URL}")
    _log(f"Konsulent: {config.consultant_name} (id={config.consultant_id})")
    _log(f"Kilde resume_id: {config.source_resume_id}")
    _log(f"Ny dummy-tittel: {config.new_title}")
    _log(f"Headless: {config.headless}")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=config.headless,
            ignore_https_errors=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await _goto_resume_all(page, config.timeout_ms)
            await _select_consultant_from_top_search(
                page,
                query=config.query,
                consultant_name=config.consultant_name,
                timeout_ms=config.timeout_ms,
            )
            _log(f"Åpnet konsulentside: {page.url}")
            await _open_cvs_tab(page, config.timeout_ms)
            await page.wait_for_timeout(500)

            before_ids = await _collect_resume_ids(page)
            _log(f"CV-er synlig før copy: {len(before_ids)}")

            row = await _find_resume_row(page, config.source_resume_id)
            await _click_copy_from_row_buttons(row, page, config.timeout_ms)
            await _confirm_copy_dialog(page, new_title=config.new_title, timeout_ms=config.timeout_ms)

            new_resume_id = await _wait_for_new_resume(
                page,
                before_ids=before_ids,
                source_resume_id=config.source_resume_id,
                timeout_ms=config.timeout_ms,
            )
            if not new_resume_id:
                raise RuntimeError("Fant ikke nyopprettet CV-id etter copy")

            _log(f"OK: Ny CV opprettet. resume_id={new_resume_id}")
            _log(f"Aktiv URL: {page.url}")
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
    cfg = _build_config()
    asyncio.run(run(cfg))
