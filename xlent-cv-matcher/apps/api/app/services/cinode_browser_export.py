from __future__ import annotations

import asyncio


class CinodeBrowserExportError(Exception):
    pass


def render_cv_page_to_pdf(cv_url: str, timeout_ms: int = 90000) -> bytes:
    if not cv_url or not cv_url.strip():
        raise CinodeBrowserExportError("Missing CV URL for browser export")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as exc:
        raise CinodeBrowserExportError(
            "Playwright is not available. Install it with: pip install playwright && python -m playwright install chromium"
        ) from exc

    async def _run() -> bytes:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            try:
                response = await page.goto(cv_url, wait_until="networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError as exc:
                await browser.close()
                raise CinodeBrowserExportError(f"Timeout loading CV page: {cv_url}") from exc

            status = response.status if response else None
            if status and status >= 400:
                await browser.close()
                raise CinodeBrowserExportError(f"Could not load CV page ({status}): {cv_url}")

            current_url = (page.url or "").lower()
            title = (await page.title() or "").lower()
            if "login" in current_url or "signin" in current_url or "logga" in title:
                await browser.close()
                raise CinodeBrowserExportError(
                    "Cinode redirected to login page. Use a public resume link or sign in before browser export."
                )

            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "12mm", "right": "10mm", "bottom": "12mm", "left": "10mm"},
            )
            await browser.close()
            return pdf_bytes

    try:
        return asyncio.run(_run())
    except CinodeBrowserExportError:
        raise
    except Exception as exc:
        raise CinodeBrowserExportError(f"Browser export failed: {exc}") from exc
