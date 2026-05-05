from __future__ import annotations

import argparse
import asyncio
import base64
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

DEFAULT_COMPANY = "xlent"
DEFAULT_ACCOUNT_NAME = "Cinode_key"
DEFAULT_HEADLESS = False
DEFAULT_TIMEOUT_MS = 45000


@dataclass
class Config:
    company: str
    account_name: str
    headless: bool
    timeout_ms: int
    output_file: Path
    show_token: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_dir() -> Path:
    folder = _repo_root() / ".run"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _log(message: str) -> None:
    print(message, flush=True)


def _mask(value: str) -> str:
    text = (value or "").strip()
    if len(text) <= 14:
        return "***"
    return f"{text[:8]}...{text[-4:]}"


def _normalize_account_name(value: str) -> str:
    text = (value or "").strip().lower()
    return re.sub(r"[\s_\-]+", "", text)


def _looks_like_basic_blob(value: str) -> bool:
    token = value.strip()
    if not token or " " in token:
        return False
    if len(token) < 20:
        return False
    try:
        pad = "=" * (-len(token) % 4)
        decoded = base64.b64decode(token + pad).decode("utf-8", errors="ignore")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    left, right = decoded.split(":", 1)
    return bool(left.strip()) and bool(right.strip())


async def _goto_start(page: Any, config: Config) -> None:
    url = f"https://app.cinode.com/{config.company}/resumes/all"
    await page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
    await page.wait_for_timeout(1200)
    if any(token in (page.url or "").lower() for token in ["login", "signin", "returnurl="]):
        raise RuntimeError("Ikke innlogget i Cinode-profilen. Logg inn i vanlig browser først.")


async def _open_my_account(page: Any, timeout_ms: int) -> None:
    avatar_candidates = [
        "button:has-text('Thomas')",
        "button:has-text('My account')",
        "header button:has(svg)",
        "button[aria-label*='account' i]",
        "button[aria-haspopup='menu']",
    ]
    opened = False
    for sel in avatar_candidates:
        node = page.locator(sel).last
        try:
            if await node.count() == 0 or not await node.is_visible(timeout=150):
                continue
            box = await node.bounding_box()
            if not box:
                continue
            # Top-right menu button usually sits in the upper header area.
            if box["y"] > 150:
                continue
            await node.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(400)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        raise RuntimeError("Fant ikke konto-meny i øvre høyre hjørne")

    my_account_selectors = [
        "button:has-text('My account')",
        "a:has-text('My account')",
        "button:has-text('Min konto')",
        "a:has-text('Min konto')",
        "button:has-text('Mitt konto')",
        "a:has-text('Mitt konto')",
    ]
    for sel in my_account_selectors:
        item = page.locator(sel).first
        try:
            if await item.count() == 0 or not await item.is_visible(timeout=250):
                continue
            await item.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(900)
            return
        except Exception:
            continue

    raise RuntimeError("Fant ikke 'My account' i konto-menyen")


async def _find_api_accounts_section(page: Any, timeout_ms: int) -> Any:
    heading = page.locator("text=/Api\\s*accounts/i").first
    for _ in range(10):
        if await heading.count() > 0 and await heading.is_visible(timeout=250):
            break
        await page.mouse.wheel(0, 1200)
        await page.wait_for_timeout(180)
    if await heading.count() == 0 or not await heading.is_visible(timeout=500):
        raise RuntimeError("Fant ikke seksjonen 'Api accounts'")

    section_candidates = [
        heading.locator("xpath=ancestor::section[1]"),
        heading.locator("xpath=ancestor::div[contains(@class, 'card')][1]"),
        heading.locator("xpath=ancestor::*[self::section or self::div][1]"),
    ]
    for candidate in section_candidates:
        try:
            if await candidate.count() > 0 and await candidate.first.is_visible(timeout=200):
                return candidate.first
        except Exception:
            continue
    return heading.locator("xpath=ancestor::*[self::section or self::div][1]").first


async def _open_api_accounts_add_dialog(page: Any, timeout_ms: int) -> None:
    section = await _find_api_accounts_section(page, timeout_ms)
    plus_selectors = [
        "button[title*='Add' i]",
        "button[aria-label*='Add' i]",
        "button.button-icon--positive",
        "button:has(svg)",
    ]

    for sel in plus_selectors:
        button = section.locator(sel).first
        try:
            if await button.count() == 0 or not await button.is_visible(timeout=180):
                continue
            await button.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(500)
            return
        except Exception:
            continue

    # Fallback: global, near heading coordinates.
    plus_global = page.locator("button.button-icon--positive, button[title*='Add' i]").first
    if await plus_global.count() > 0 and await plus_global.is_visible(timeout=300):
        await plus_global.click(timeout=min(timeout_ms, 5000), force=True)
        await page.wait_for_timeout(500)
        return

    raise RuntimeError("Fant ikke + ved 'Api accounts'")


async def _find_api_account_row(section: Any, account_name: str) -> Any | None:
    wanted = _normalize_account_name(account_name)
    name_matches = section.locator(f"text=/{re.escape(account_name)}/i")
    count = await name_matches.count()
    for i in range(min(count, 60)):
        node = name_matches.nth(i)
        try:
            if not await node.is_visible(timeout=120):
                continue
        except Exception:
            continue

        row_candidates = [
            node.locator("xpath=ancestor::tr[1]"),
            node.locator("xpath=ancestor::li[1]"),
            node.locator("xpath=ancestor::div[.//button][1]"),
        ]
        for row in row_candidates:
            try:
                if await row.count() == 0 or not await row.first.is_visible(timeout=100):
                    continue
                text = " ".join(((await row.first.inner_text()) or "").split()).lower()
                if wanted and wanted in _normalize_account_name(text):
                    return row.first
            except Exception:
                continue
    # Broader fallback: scan likely rows and compare normalized text.
    all_rows = section.locator("tr, li, div")
    rcount = await all_rows.count()
    for i in range(min(rcount, 350)):
        row = all_rows.nth(i)
        try:
            if not await row.is_visible(timeout=40):
                continue
            text = " ".join(((await row.inner_text()) or "").split())
            if not text:
                continue
            if wanted and wanted in _normalize_account_name(text):
                # Must look like an actionable row with at least one button.
                if await row.locator("button").count() > 0:
                    return row
        except Exception:
            continue
    return None


async def _open_row_menu(row: Any, timeout_ms: int) -> bool:
    explicit_selectors = [
        "button[aria-label*='more' i]",
        "button[title*='more' i]",
        "button[aria-label*='options' i]",
        "button[title*='options' i]",
        "button:has-text('more_vert')",
        "button:has(svg)",
    ]
    for sel in explicit_selectors:
        btn = row.locator(sel).last
        try:
            if await btn.count() == 0 or not await btn.is_visible(timeout=100):
                continue
            await btn.click(timeout=min(timeout_ms, 3500), force=True)
            await asyncio.sleep(0.25)
            return True
        except Exception:
            continue

    buttons = row.locator("button")
    count = await buttons.count()
    best_idx = -1
    best_x = -1.0
    for i in range(min(count, 30)):
        btn = buttons.nth(i)
        try:
            if not await btn.is_visible(timeout=80):
                continue
            box = await btn.bounding_box()
            if not box:
                continue
            if box["x"] > best_x:
                best_x = box["x"]
                best_idx = i
        except Exception:
            continue

    if best_idx < 0:
        return False

    try:
        await buttons.nth(best_idx).click(timeout=min(timeout_ms, 4000), force=True)
        await asyncio.sleep(0.25)
        return True
    except Exception:
        return False


async def _click_remove_in_menu(page: Any, timeout_ms: int) -> bool:
    menu = page.locator("[role='menu']:visible, .dropdown-menu:visible, .menu:visible").last
    scope = menu if await menu.count() > 0 else page
    selectors = [
        "[role='menuitem']:has-text('Remove')",
        "[role='menuitem']:has-text('Delete')",
        "[role='menuitem']:has-text('Fjern')",
        "[role='menuitem']:has-text('Ta bort')",
        "button:has-text('Remove')",
        "a:has-text('Remove')",
        "button:has-text('Delete')",
        "a:has-text('Delete')",
        "button:has-text('Fjern')",
        "a:has-text('Fjern')",
        "button:has-text('Ta bort')",
        "a:has-text('Ta bort')",
    ]
    for sel in selectors:
        item = scope.locator(sel).first
        try:
            if await item.count() == 0 or not await item.is_visible(timeout=150):
                continue
            await item.click(timeout=min(timeout_ms, 4000), force=True)
            await asyncio.sleep(0.35)
            return True
        except Exception:
            continue

    generic = scope.locator("text=/\\b(Remove|Delete|Fjern|Ta bort)\\b/i").first
    try:
        if await generic.count() > 0 and await generic.is_visible(timeout=120):
            await generic.click(timeout=min(timeout_ms, 3000), force=True)
            await asyncio.sleep(0.35)
            return True
    except Exception:
        pass
    return False


async def _find_remove_dialog(page: Any) -> Any | None:
    dialogs = page.locator("[role='dialog']:visible, app-modal-anchor:visible, .modal:visible, .modal-body-component:visible")
    count = await dialogs.count()
    for i in range(min(count, 10) - 1, -1, -1):
        d = dialogs.nth(i)
        try:
            if not await d.is_visible(timeout=60):
                continue
            text = " ".join(((await d.inner_text()) or "").split()).lower()
            if any(
                token in text
                for token in [
                    "are you sure you want to remove",
                    "remove",
                    "delete",
                    "fjern",
                    "ta bort",
                ]
            ):
                return d
        except Exception:
            continue
    return None


async def _force_close_remove_dialog(page: Any, timeout_ms: int) -> bool:
    deadline = asyncio.get_event_loop().time() + max(2.0, timeout_ms / 1000)
    while asyncio.get_event_loop().time() < deadline:
        dialog = await _find_remove_dialog(page)
        if dialog is None:
            return True
        clicked = False
        selectors = [
            "button:has-text('Ok')",
            "button:has-text('OK')",
            "button:has-text('Yes')",
            "button:has-text('Ja')",
            "button:has-text('Remove')",
            "button:has-text('Delete')",
        ]
        for sel in selectors:
            btn = dialog.locator(sel).first
            try:
                if await btn.count() == 0 or not await btn.is_visible(timeout=90):
                    continue
                await btn.click(timeout=1500, force=True)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            # Fallback: choose first visible non-cancel button in dialog
            buttons = dialog.locator("button")
            bcount = await buttons.count()
            for i in range(min(bcount, 8)):
                btn = buttons.nth(i)
                try:
                    if not await btn.is_visible(timeout=60):
                        continue
                    text = ((await btn.inner_text()) or "").strip().lower()
                    if text in {"cancel", "avbryt"}:
                        continue
                    await btn.click(timeout=1500, force=True)
                    clicked = True
                    break
                except Exception:
                    continue
        if not clicked:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
        await asyncio.sleep(0.35)
    return False


async def _confirm_remove_dialog(page: Any, timeout_ms: int) -> None:
    dialog = await _find_remove_dialog(page)
    if dialog is None:
        return
    selectors = [
        "button:has-text('Remove')",
        "button:has-text('Delete')",
        "button:has-text('Yes')",
        "button:has-text('OK')",
        "button:has-text('Ok')",
        "button:has-text('Ja')",
    ]
    clicked = False
    for sel in selectors:
        btn = dialog.locator(sel).first
        try:
            if await btn.count() == 0 or not await btn.is_visible(timeout=120):
                continue
            await btn.click(timeout=min(timeout_ms, 4000), force=True)
            await asyncio.sleep(0.45)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        confirm_text = dialog.locator("text=/\\b(Remove|Delete|Yes|OK|Ok|Ja|Fjern|Ta bort)\\b/i").first
        try:
            if await confirm_text.count() > 0 and await confirm_text.is_visible(timeout=120):
                await confirm_text.click(timeout=min(timeout_ms, 3000), force=True)
                await asyncio.sleep(0.45)
                clicked = True
        except Exception:
            pass

    # If button click failed, try Enter as explicit confirm fallback.
    if not clicked:
        try:
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.4)
            clicked = True
        except Exception:
            pass

    if not await _force_close_remove_dialog(page, timeout_ms):
        raise RuntimeError("Remove-dialog ble ikke lukket etter bekreftelse")


async def _dismiss_blocking_modal_if_any(page: Any) -> None:
    modal = await _find_remove_dialog(page)
    if modal is None:
        return
    await _confirm_remove_dialog(page, 5000)


async def _remove_existing_api_accounts(page: Any, account_name: str, timeout_ms: int) -> int:
    section = await _find_api_accounts_section(page, timeout_ms)
    removed = 0
    for _ in range(2):
        row = await _find_api_account_row(section, account_name)
        if row is None:
            _log(f"Ingen eksisterende API account funnet for navn: {account_name}")
            break
        row_text = " ".join(((await row.inner_text()) or "").split())[:180]
        _log(f"Fant eksisterende account-rad: {row_text}")
        if not await _open_row_menu(row, timeout_ms):
            _log("ADVARSEL: Klarte ikke åpne 3-prikk meny for raden")
            break
        if not await _click_remove_in_menu(page, timeout_ms):
            _log("ADVARSEL: Fant ikke 'Remove' i menyen")
            break
        await _confirm_remove_dialog(page, timeout_ms)
        if not await _force_close_remove_dialog(page, 2500):
            raise RuntimeError("Remove-dialog er fortsatt åpen etter sletting")
        await page.wait_for_timeout(900)
        row_after = await _find_api_account_row(section, account_name)
        if row_after is None:
            removed += 1
            _log("Slettet account-rad")
            break
        else:
            _log("Account-rad finnes fortsatt etter sletting; prøver én gang til")
    return removed


async def _create_api_account(page: Any, name: str, timeout_ms: int) -> None:
    await _dismiss_blocking_modal_if_any(page)
    if not await _force_close_remove_dialog(page, 2500):
        raise RuntimeError("Kan ikke åpne API account-dialog mens remove-dialog er aktiv")
    modals = page.locator(
        "[role='dialog']:visible, app-modal-anchor:visible, .modal:visible, .modal-body-component:visible"
    )
    mcount = await modals.count()
    if mcount == 0:
        raise RuntimeError("Fant ikke dialog for oppretting av API account")

    modal = modals.last
    # Prefer a modal that likely contains the add-account form.
    for i in range(mcount):
        candidate = modals.nth(i)
        try:
            text = " ".join(((await candidate.inner_text()) or "").split()).lower()
            if any(token in text for token in ["api account", "encoded credentials"]):
                modal = candidate
                break
            if await candidate.locator("input[type='text'], input[placeholder*='name' i], input[name*='name' i]").count() > 0:
                modal = candidate
                break
        except Exception:
            continue

    input_selectors = [
        "input[placeholder*='Name' i]",
        "input[aria-label*='Name' i]",
        "input[name*='name' i]",
        "input[type='text']",
        "input",
    ]
    filled = False
    for sel in input_selectors:
        field = modal.locator(sel).first
        try:
            if await field.count() == 0 or not await field.is_visible(timeout=180):
                continue
            await field.click(timeout=2000)
            await field.fill("")
            await field.type(name, delay=25)
            filled = True
            break
        except Exception:
            continue
    if not filled:
        raise RuntimeError("Fant ikke Name-felt i API account-dialogen")

    save_selectors = [
        "button:has-text('Save')",
        "button:has-text('Create')",
        "button:has-text('Lagre')",
    ]
    for sel in save_selectors:
        btn = modal.locator(sel).first
        try:
            if await btn.count() == 0 or not await btn.is_visible(timeout=250):
                continue
            await btn.click(timeout=min(timeout_ms, 5000), force=True)
            await page.wait_for_timeout(900)
            return
        except Exception:
            continue

    raise RuntimeError("Fant ikke Save/Create-knapp i API account-dialogen")


async def _extract_encoded_credentials(page: Any, timeout_ms: int) -> str:
    # After save, Cinode typically opens a follow-up modal containing "Encoded Credentials".
    modal = page.locator(
        "[role='dialog']:visible, app-modal-anchor:visible, .modal:visible, .modal-body-component:visible"
    ).last

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < max(5.0, timeout_ms / 1000):
        try:
            if await modal.count() > 0 and await modal.is_visible(timeout=200):
                # Strong selectors first.
                selectors = [
                    "input[readonly]",
                    "textarea[readonly]",
                    "code",
                    "pre",
                    "input[type='text']",
                    "textarea",
                ]
                for sel in selectors:
                    node = modal.locator(sel)
                    count = await node.count()
                    for i in range(min(count, 20)):
                        item = node.nth(i)
                        if not await item.is_visible(timeout=60):
                            continue
                        value = (
                            (await item.input_value()) if sel.startswith("input") or sel.startswith("textarea") else (await item.inner_text())
                        )
                        token = (value or "").strip()
                        if not token:
                            continue
                        if token.lower().startswith("basic "):
                            token = token.split(" ", 1)[1].strip()
                        if _looks_like_basic_blob(token):
                            return token

                # Text scan fallback.
                text = " ".join(((await modal.inner_text()) or "").split())
                matches = re.findall(r"([A-Za-z0-9+/=]{30,})", text)
                for candidate in matches:
                    if _looks_like_basic_blob(candidate):
                        return candidate
        except Exception:
            pass

        await page.wait_for_timeout(300)

    raise RuntimeError("Fant ikke 'Encoded Credentials' i resultatdialogen")


def _write_output(config: Config, encoded_credentials: str) -> None:
    basic_header_value = f"Basic {encoded_credentials}"
    content = (
        f"created_at={datetime.now().isoformat()}\n"
        f"company={config.company}\n"
        f"api_account_name={config.account_name}\n"
        f"encoded_credentials={encoded_credentials}\n"
        f"cinode_api_token={basic_header_value}\n"
    )
    config.output_file.parent.mkdir(parents=True, exist_ok=True)
    config.output_file.write_text(content, encoding="utf-8")


async def run(config: Config) -> None:
    profile_dir = _run_dir() / "cinode-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Start: https://app.cinode.com/{config.company}/resumes/all")
    _log(f"API account name: {config.account_name}")
    _log(f"Output file: {config.output_file}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=config.headless,
            ignore_https_errors=True,
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        try:
            await _goto_start(page, config)
            await _open_my_account(page, config.timeout_ms)
            _log("Åpnet My account")

            removed = await _remove_existing_api_accounts(page, config.account_name, config.timeout_ms)
            if removed > 0:
                _log(f"Slettet eksisterende API account(s): {removed}")
            else:
                _log("Ingen eksisterende API account med samme navn funnet")

            await _open_api_accounts_add_dialog(page, config.timeout_ms)
            _log("Åpnet + ved Api accounts")

            await _create_api_account(page, config.account_name, config.timeout_ms)
            _log("Opprettet API account")

            encoded = await _extract_encoded_credentials(page, config.timeout_ms)
            _write_output(config, encoded)

            _log("OK: Encoded Credentials hentet")
            _log(f"Maskert: {_mask(encoded)}")
            if config.show_token:
                _log("")
                _log("CINODE_API_TOKEN (bruk denne):")
                _log(f"Basic {encoded}")
                _log("")
            _log(f"Lagret i: {config.output_file}")
        except Exception:
            shot = _run_dir() / "cinode-token-bootstrap-failure.png"
            try:
                await page.screenshot(path=str(shot), full_page=True)
                _log(f"Skjermbilde: {shot}")
            except Exception:
                pass
            raise
        finally:
            await browser.close()


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Testscript: Opprett Cinode API account via browser og hent Encoded Credentials."
    )
    parser.add_argument("--company", default=DEFAULT_COMPANY, help="Cinode tenant/company slug (default: xlent)")
    parser.add_argument("--name", default=DEFAULT_ACCOUNT_NAME, help="Navn på API account (default: Cinode_key)")
    parser.add_argument("--headless", action="store_true", help="Kjør headless")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="Timeout per steg")
    parser.add_argument(
        "--output-file",
        default=str(_run_dir() / "cinode-token-bootstrap.txt"),
        help="Fil der token/metadata lagres",
    )
    parser.add_argument(
        "--hide-token",
        action="store_true",
        help="Skjul full token i terminalutskrift (lagres fortsatt til output-fil)",
    )
    args = parser.parse_args()

    return Config(
        company=args.company.strip() or DEFAULT_COMPANY,
        account_name=args.name.strip() or DEFAULT_ACCOUNT_NAME,
        headless=bool(args.headless),
        timeout_ms=max(10000, int(args.timeout_ms)),
        output_file=Path(args.output_file).expanduser(),
        show_token=not bool(args.hide_token),
    )


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
