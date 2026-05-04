from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _log(msg: str) -> None:
    print(msg, flush=True)


async def _ensure_cv_list_page(page: Any, timeout_ms: int) -> None:
    # If we're not on /resumes, use in-app navigation to CV list.
    if "/resumes" in (page.url or "").lower():
        return
    # Try top nav "CVs"
    top_candidates = [
        "a:has-text('CVs')",
        "button:has-text('CVs')",
        "a:has-text('CV')",
        "button:has-text('CV')",
    ]
    for selector in top_candidates:
        item = page.locator(selector).first
        try:
            if await item.count() == 0:
                continue
            if not await item.is_visible(timeout=300):
                continue
            await item.click(timeout=timeout_ms)
            await page.wait_for_timeout(900)
            if "/resumes" in (page.url or "").lower():
                return
        except Exception:
            continue

    # Try left menu CV -> Yours
    side_candidates = [
        "a:has-text('CV')",
        "button:has-text('CV')",
        "a:has-text('Yours')",
    ]
    for selector in side_candidates:
        item = page.locator(selector).first
        try:
            if await item.count() == 0:
                continue
            if not await item.is_visible(timeout=300):
                continue
            await item.click(timeout=timeout_ms)
            await page.wait_for_timeout(700)
            if "/resumes" in (page.url or "").lower():
                return
        except Exception:
            continue

    # Final hard navigation fallback:
    current = page.url or ""
    if current.startswith("https://app.cinode.com/"):
        try:
            tenant = current.split("https://app.cinode.com/")[1].split("/")[0]
            await page.goto(f"https://app.cinode.com/{tenant}/resumes", wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(800)
        except Exception:
            pass


async def _click_row_menu_and_edit(page: Any, cv_title: str, timeout_ms: int) -> int:
    wanted = cv_title.strip().lower()
    wanted_prefix = " ".join(cv_title.strip().split()[:4]).lower()
    resume_id = 0

    # Wait briefly for list rendering.
    found_label = None
    for _ in range(12):
        label_candidates = [
            page.locator(f"text=/{' '.join(cv_title.split()[:3])}/i").first,
            page.locator(f"text=/{' '.join(cv_title.split()[:2])}/i").first,
        ]
        for candidate in label_candidates:
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=150):
                    found_label = candidate
                    break
            except Exception:
                continue
        if found_label is not None:
            break
        await page.wait_for_timeout(400)

    if found_label is None:
        raise RuntimeError(f"Fant ikke CV med tittel: {cv_title}.")

    row = None
    # Find the closest useful container that contains row text and action buttons.
    for up in [1, 2, 3, 4, 5, 6]:
        try:
            candidate_row = found_label.locator(f"xpath=ancestor::*[{up}]")
            if await candidate_row.count() == 0:
                continue
            if not await candidate_row.is_visible(timeout=100):
                continue
            text = " ".join((((await candidate_row.inner_text()) or "").strip()).split())
            lower = text.lower()
            buttons = candidate_row.locator("button")
            bcount = await buttons.count()
            if bcount >= 2 and (wanted in lower or wanted_prefix in lower):
                row = candidate_row
                break
        except Exception:
            continue

    if row is None:
        # Fallback: use nearest div ancestor with at least 2 buttons.
        for up in [1, 2, 3, 4, 5, 6]:
            try:
                candidate_row = found_label.locator(f"xpath=ancestor::div[{up}]")
                if await candidate_row.count() == 0:
                    continue
                buttons = candidate_row.locator("button")
                if await buttons.count() >= 2:
                    row = candidate_row
                    break
            except Exception:
                continue

    if row is None:
        raise RuntimeError("Fant ikke CV-rad/container for valgt tittel")

    async def _open_row_menu() -> bool:
        buttons = row.locator("button")
        try:
            bcount = await buttons.count()
        except Exception:
            bcount = 0
        if bcount <= 0:
            return False
        for idx in range(bcount - 1, max(-1, bcount - 4), -1):
            btn = buttons.nth(idx)
            try:
                if not await btn.is_visible(timeout=300):
                    continue
                await btn.click(timeout=timeout_ms)
                await page.wait_for_timeout(350)
                return True
            except Exception:
                continue
        return False

    async def _menu_visible_items() -> list[Any]:
        menu_roots = page.locator("[role='menu'], [role='listbox'], [data-state='open']")
        try:
            mcount = await menu_roots.count()
        except Exception:
            mcount = 0
        menu_root = menu_roots.last if mcount > 0 else page
        candidates = menu_root.locator("[role='menuitem'], li, a, button, [role='option']")
        try:
            ccount = await candidates.count()
        except Exception:
            ccount = 0
        visible_items: list[Any] = []
        for i in range(min(ccount, 40)):
            item = candidates.nth(i)
            try:
                if not await item.is_visible(timeout=150):
                    continue
                txt = ((await item.inner_text()) or "").strip()
                if not txt or txt in {"-", "—", "|"}:
                    continue
                visible_items.append(item)
            except Exception:
                continue
        return visible_items

    opened = await _open_row_menu()
    if not opened:
        raise RuntimeError("Klarte ikke åpne 3-prikk meny")

    # Attempt 0: keyboard navigation in menu (Edit is 2nd item in your UI).
    try:
        await page.keyboard.press("ArrowDown")
        await page.wait_for_timeout(120)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1000)
        current = page.url or ""
        if "/resumes/user/" in current and "/edit/" in current:
            return resume_id
    except Exception:
        pass

    # Attempt 1: click second menu item (Edit according to your UI).
    items = await _menu_visible_items()
    if len(items) >= 2:
        try:
            await items[1].click(timeout=timeout_ms)
            await page.wait_for_timeout(1000)
            current = page.url or ""
            if "/resumes/user/" in current and "/edit/" in current:
                return resume_id
        except Exception:
            pass

    # Attempt 2: reopen menu and try text-based Edit.
    opened = await _open_row_menu()
    if opened:
        items = await _menu_visible_items()
        for item in items:
            try:
                txt = ((await item.inner_text()) or "").strip()
            except Exception:
                txt = ""
            if re.search(r"\b(Edit|Rediger|Redigera)\b", txt, flags=re.IGNORECASE):
                try:
                    await item.click(timeout=timeout_ms)
                    await page.wait_for_timeout(1000)
                    current = page.url or ""
                    if "/resumes/user/" in current and "/edit/" in current:
                        return resume_id
                except Exception:
                    continue

    # Attempt 3: direct pen icon click fallback from row.
    pen_selectors = [
        "button[aria-label*='edit' i]",
        "button[title*='edit' i]",
        "button[aria-label*='pen' i]",
        "button[title*='pen' i]",
    ]
    for selector in pen_selectors:
        pen = row.locator(selector).first
        try:
            if await pen.count() == 0:
                continue
            if not await pen.is_visible(timeout=250):
                continue
            await pen.click(timeout=timeout_ms)
            await page.wait_for_timeout(900)
            current = page.url or ""
            if "/resumes/user/" in current and "/edit/" in current:
                return resume_id
        except Exception:
            continue

    # Debug: print menu text if available.
    try:
        menu_text = ""
        menu = page.locator("[role='menu']").last
        if await menu.count() > 0:
            menu_text = ((await menu.inner_text()) or "").strip()
        if menu_text:
            raise RuntimeError(f"Fant ikke 'Edit' i 3-prikk meny. Menyinnhold: {' '.join(menu_text.split())[:300]}")
        # If no role=menu, dump visible popup-ish texts for diagnostics.
        pop = page.locator("[role='listbox'], [data-state='open'], .menu, .dropdown-menu").last
        if await pop.count() > 0:
            pop_text = ((await pop.inner_text()) or "").strip()
            if pop_text:
                raise RuntimeError(f"Fant ikke 'Edit' i 3-prikk meny. Popupinnhold: {' '.join(pop_text.split())[:300]}")
    except RuntimeError:
        raise
    except Exception:
        pass
    raise RuntimeError("Fant ikke 'Edit' i 3-prikk meny")


async def _open_category_dialog(page: Any, timeout_ms: int) -> Any:
    async def _find_skills_dialog_scope() -> Any | None:
        # Cinode bruker ofte en åpen høyre-drawer (mat-sidenav) i stedet for role=dialog.
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

    # Deterministisk flyt:
    # 1) finn seksjonen "Ferdigheter etter kategori"
    # 2) finn "Teknikker" (evt "Produkter") under denne
    # 3) klikk "Edit" i samme rad
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    except Exception:
        pass
    section = None
    for _ in range(40):
        heading = page.locator("text=/Ferdigheter etter kategori|Skills by category/i").first
        try:
            if await heading.count() > 0 and await heading.is_visible(timeout=150):
                section = heading.locator("xpath=ancestor::*[self::section or self::div][1]")
                break
        except Exception:
            pass
        try:
            await page.keyboard.press("PageDown")
        except Exception:
            await page.mouse.wheel(0, 700)
        await page.wait_for_timeout(220)

    if section is None:
        raise RuntimeError("Fant ikke seksjonen 'Ferdigheter etter kategori' i editor")

    # Finn kategori innenfor seksjonen.
    target = None
    for pattern in [r"^Teknikker$", r"^Produkter$", r"Teknikker", r"Produkter"]:
        lbl = section.locator(f"text=/{pattern}/i").first
        try:
            if await lbl.count() > 0 and await lbl.is_visible(timeout=250):
                target = lbl
                break
        except Exception:
            continue

    if target is None:
        # fallback: globalt hvis section-scope er trangt
        for pattern in [r"^Teknikker$", r"^Produkter$", r"Teknikker", r"Produkter"]:
            lbl = page.locator(f"text=/{pattern}/i").first
            try:
                if await lbl.count() > 0 and await lbl.is_visible(timeout=250):
                    target = lbl
                    break
            except Exception:
                continue

    if target is None:
        raise RuntimeError("Fant ikke kategori-etikett 'Teknikker' eller 'Produkter' under seksjonen")

    await target.scroll_into_view_if_needed(timeout=timeout_ms)
    await page.wait_for_timeout(200)

    # I Cinode ligger "Edit" i samme item-rad.
    for up in [3, 4, 5, 6, 7, 8]:
        row = target.locator(f"xpath=ancestor::*[{up}]")
        try:
            if await row.count() == 0:
                continue
            edit = row.locator("text=/^Edit$/i").first
            if await edit.count() > 0 and await edit.is_visible(timeout=250):
                await edit.click(timeout=timeout_ms)
                for _ in range(12):
                    await page.wait_for_timeout(250)
                    scope = await _find_skills_dialog_scope()
                    if scope is not None:
                        return scope
        except Exception:
            continue

    raise RuntimeError("Fant 'Teknikker/Produkter' under seksjonen, men klarte ikke klikke 'Edit' i samme rad")


async def _add_skill_and_check(page: Any, dialog_scope: Any, skill: str, timeout_ms: int) -> None:
    def _norm(s: str) -> str:
        return " ".join((s or "").strip().lower().split())

    async def _set_level_five(container: Any) -> None:
        # Prøv først direkte nivå-knapp i raden.
        try:
            direct_five = container.locator(
                "button:has-text('5'), [role='button']:has-text('5')"
            ).first
            if await direct_five.count() > 0 and await direct_five.is_visible(timeout=150):
                await direct_five.click(timeout=min(timeout_ms, 1800))
                await page.wait_for_timeout(120)
                return
        except Exception:
            pass

        # Ellers: åpne "Edit level"/nivådialog og velg 5 der.
        try:
            edit_level = container.locator(
                "button[aria-label*='Edit level' i], button[title*='Edit level' i], button[aria-label*='Level' i], button[title*='Level' i]"
            ).first
            if await edit_level.count() > 0 and await edit_level.is_visible(timeout=150):
                await edit_level.click(timeout=min(timeout_ms, 1800))
                await page.wait_for_timeout(180)
        except Exception:
            pass

        try:
            level_scope = dialog_scope
            popup = page.locator("text=/Level settings|Nivåinnstillinger/i").first
            if await popup.count() > 0 and await popup.is_visible(timeout=120):
                level_scope = popup.locator("xpath=ancestor::div[1]")
            five = level_scope.locator(
                "button:has-text('5'), [role='button']:has-text('5')"
            ).first
            if await five.count() > 0 and await five.is_visible(timeout=180):
                await five.click(timeout=min(timeout_ms, 1800))
                await page.wait_for_timeout(120)
        except Exception:
            pass

    # Lukk eventuelle nivå-popups som kan blokkere input/klikk i drawer.
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

    # Hvis skill allerede finnes i listen, hopp over add-flyt og bare huk av.
    existing = dialog_scope.locator(f"text=/{re.escape(skill)}/i").first
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

    label_patterns = [r"Skills to include", r"Ferdigheter som skal inkluderes"]
    plus_selectors = [
        # Eksplisitt: '+' ved "Skills to include"
        "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'skills to include')]/following::button[contains(@class,'button-icon')][1]",
        "xpath=.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÆØÅ', 'abcdefghijklmnopqrstuvwxyzæøå'), 'ferdigheter som skal inkluderes')]/following::button[contains(@class,'button-icon')][1]",
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

    clicked_plus = False
    for root in [dialog_scope, include_scope]:
        for selector in plus_selectors:
            plus = root.locator(selector).last
            try:
                if await plus.count() == 0:
                    continue
                if not await plus.is_visible(timeout=300):
                    continue
                try:
                    await plus.click(timeout=timeout_ms)
                except Exception:
                    await plus.click(timeout=timeout_ms, force=True)
                await page.wait_for_timeout(300)
                clicked_plus = True
                break
            except Exception:
                continue
        if clicked_plus:
            break
    if not clicked_plus:
        raise RuntimeError("Fant ikke grønn + ved 'Skills to include'")

    typed = False
    typed_input = None
    # Retry-loop: etter '+' skal Add skill-feltet være "Try typing 'Project management'".
    for _ in range(4):
        inp = dialog_scope.locator(
            "input[placeholder*='Try typing' i], input[placeholder*='Project management' i]"
        ).first
        try:
            if await inp.count() > 0 and await inp.is_visible(timeout=350):
                await inp.fill("", timeout=timeout_ms)
                await inp.type(skill, delay=20, timeout=timeout_ms)
                typed = True
                typed_input = inp
                break
        except Exception:
            pass
        # Try one extra '+' click to force opening Add skill input.
        try:
            plus_retry = dialog_scope.locator("cui-button-icon[title*='Add' i] button, button.button-icon--positive").first
            if await plus_retry.count() > 0 and await plus_retry.is_visible(timeout=150):
                await plus_retry.click(timeout=1200, force=True)
                await page.wait_for_timeout(220)
        except Exception:
            pass
    if not typed:
        raise RuntimeError("Fant ikke 'Add skill' inputfelt")

    # Eksplisitt bekreftelse: klikk "Add" under inputfeltet når tilgjengelig.
    add_clicked = False
    add_confirm_selectors = [
        f"text=/^Add\\s+{re.escape(skill)}$/i",
        "text=/^Add$/i",
        "button:has-text('Add')",
        "[role='option']:has-text('Add')",
        "li:has-text('Add')",
    ]
    for selector in add_confirm_selectors:
        cand = dialog_scope.locator(selector).first
        try:
            if await cand.count() == 0:
                continue
            if not await cand.is_visible(timeout=200):
                continue
            await cand.click(timeout=min(timeout_ms, 2000))
            await page.wait_for_timeout(250)
            add_clicked = True
            break
        except Exception:
            continue

    chosen = False
    option_selectors = [
        f"[role='option']:has-text('{skill}')",
        f"[role='menuitem']:has-text('{skill}')",
        f"text=/Add\\s+{re.escape(skill)}/i",
        f"text=/Create\\s+{re.escape(skill)}/i",
        f"li:has-text('{skill}')",
    ]
    for selector in option_selectors:
        opt = dialog_scope.locator(selector).first
        try:
            if await opt.count() == 0:
                continue
            if not await opt.is_visible(timeout=300):
                continue
            await opt.click(timeout=timeout_ms)
            chosen = True
            break
        except Exception:
            continue
    if not chosen and not add_clicked:
        try:
            if typed_input is not None:
                await typed_input.press("ArrowDown", timeout=1200)
                await typed_input.press("Enter", timeout=1200)
                chosen = True
        except Exception:
            pass
    if not chosen and not add_clicked:
        try:
            if typed_input is not None:
                await typed_input.press("Enter", timeout=1500)
                chosen = True
        except Exception:
            pass
    if not chosen and not add_clicked:
        try:
            # Some UI variants add directly without explicit dropdown selection.
            await page.wait_for_timeout(500)
            chosen = True
        except Exception:
            pass

    await page.wait_for_timeout(500)

    # Rydd søkefilter hvis det ble satt, så vi ikke skjuler ny rad.
    try:
        search_inp = dialog_scope.locator("input[placeholder*='Search' i]").first
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=120):
            cur = await search_inp.input_value()
            if cur.strip():
                await search_inp.fill("", timeout=timeout_ms)
                await page.wait_for_timeout(150)
    except Exception:
        pass

    # Ensure all skills are visible for verification.
    for tab_text in ["All", "Alle"]:
        try:
            tab = dialog_scope.locator(f"text=/{tab_text}\\s*\\(/i").first
            if await tab.count() > 0 and await tab.is_visible(timeout=120):
                await tab.click(timeout=1200)
                await page.wait_for_timeout(180)
                break
        except Exception:
            continue

    # Bruk søkefelt i listen for å finne raden deterministisk.
    try:
        search_inp = dialog_scope.locator("input[placeholder*='Search' i]").first
        if await search_inp.count() > 0 and await search_inp.is_visible(timeout=150):
            await search_inp.fill("", timeout=timeout_ms)
            await search_inp.type(skill, delay=15, timeout=timeout_ms)
            await page.wait_for_timeout(220)
    except Exception:
        pass

    # Check the newly added skill in list area (ikke Add-skill inputområdet).
    async def _find_skill_container() -> Any | None:
        matches = dialog_scope.locator(f"text=/{re.escape(skill)}/i")
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
            # Begrens oppoverstigning for å unngå at hele dialogen matches.
            for up in [1, 2, 3, 4]:
                c = m.locator(f"xpath=ancestor::*[{up}]")
                try:
                    if await c.count() == 0:
                        continue
                    txt = " ".join((((await c.inner_text()) or "").strip()).split())
                    low = txt.lower()
                    # Ignorer Add-skill/søk-boksen.
                    if "add skill" in low or "skills to include" in low:
                        continue
                    # Må være en skill-rad/toggle-rad.
                    if _norm(skill) not in _norm(low):
                        continue
                    # Rad bør ha minst ett kontroll-element på høyresiden.
                    ctrl = c.locator(
                        "input[type='checkbox'], [role='checkbox'], button:has(svg use[href*='check']), button[class*='positive'], button[aria-label*='select' i], button[title*='select' i], button[aria-label*='include' i], button[title*='include' i], button[class*='toggle'], button[class*='check']"
                    )
                    # Unngå rad som er selve søkefelt/add skill.
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
            break
        try:
            if typed_input is not None:
                await typed_input.press("Enter", timeout=1200)
        except Exception:
            pass
        await page.wait_for_timeout(300)

    if container is None:
        raise RuntimeError(f"Fant ikke skill-rad etter add: {skill}")

    cb = container.locator("input[type='checkbox'], [role='checkbox']").first
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
        await _set_level_five(container)
        return

    # Fallback: Cinode kan bruke knapp-toggle i stedet for checkbox-input.
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
    await _set_level_five(container)

    # Endelig verifisering: skill skal være synlig i "Selected"-fanen.
    try:
        selected_tab = dialog_scope.locator("text=/Selected\\s*\\(/i").first
        if await selected_tab.count() > 0 and await selected_tab.is_visible(timeout=120):
            await selected_tab.click(timeout=1200)
            await page.wait_for_timeout(200)
        s = dialog_scope.locator(f"text=/{re.escape(skill)}/i").first
        if await s.count() == 0 or not await s.is_visible(timeout=400):
            raise RuntimeError(f"Skill ikke aktiv i Selected etter avhuking: {skill}")
    except Exception:
        raise


async def run(args: argparse.Namespace) -> None:
    repo = _repo_root()
    run_dir = repo / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = run_dir / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    shot_path = run_dir / "skill-test-failure.png"

    start_url = f"{args.app_url.rstrip('/')}/{args.company_slug}/resumes"
    _log(f"Start: {start_url}")
    _log(f"CV: {args.cv_title}")
    _log(f"Skill: {args.skill}")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            ignore_https_errors=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            await page.wait_for_timeout(900)
            await _ensure_cv_list_page(page, args.timeout_ms)
            _log(f"Aktiv side: {page.url}")

            resume_id = await _click_row_menu_and_edit(page, args.cv_title, args.timeout_ms)
            _log(f"Åpnet Edit for CV (resume_id={resume_id or 'ukjent'})")

            dialog = await _open_category_dialog(page, args.timeout_ms)
            _log("Åpnet kategori-dialog for ferdigheter")

            await _add_skill_and_check(page, dialog, args.skill, args.timeout_ms)
            _log("La til og huket av skill")

            # Save if available
            save_candidates = [
                "button:has-text('Save')",
                "button:has-text('Lagre')",
                "button:has-text('Spara')",
                "a:has-text('Save')",
                "a:has-text('Lagre')",
            ]
            saved = False
            for selector in save_candidates:
                btn = page.locator(selector).first
                try:
                    if await btn.count() == 0:
                        continue
                    if not await btn.is_visible(timeout=300):
                        continue
                    await btn.click(timeout=args.timeout_ms)
                    await page.wait_for_timeout(800)
                    saved = True
                    break
                except Exception:
                    continue
            _log("Lagret endringer" if saved else "Ingen eksplisitt Save funnet (kan være autosave)")
            _log("FERDIG: Skill-flyt fullført")
        except Exception as exc:
            try:
                await page.screenshot(path=str(shot_path), full_page=True)
            except Exception:
                pass
            _log(f"FEIL: {exc}")
            _log(f"Skjermbilde: {shot_path}")
            raise
        finally:
            await context.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Testscript: legg til én skill i valgt Cinode-CV")
    parser.add_argument("--app-url", default="https://app.cinode.com")
    parser.add_argument("--company-slug", default="xlent")
    parser.add_argument(
        "--cv-title",
        default="Thomas Elboth - Senior Konsulent - Integrasjon",
    )
    parser.add_argument("--skill", default="tull og tøys")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--headless", action="store_true", help="Kjør headless")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
