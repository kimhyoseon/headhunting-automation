from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.models import Candidate


@dataclass(slots=True)
class RememberBrowserConfig:
    cdp_url: str = "http://127.0.0.1:9222"
    remember_url: str = "https://career.rememberapp.co.kr/"
    locale: str = "ko-KR"
    accept_language: str = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    timezone: str = "Asia/Seoul"


class BrowserRememberAdapter:
    """Future adapter for a user-controlled Remember browser tab.

    The current MVP still uses MockRememberAdapter. This class defines the
    connection shape that will replace it once Remember selectors can be
    verified on an accessible client PC.
    """

    def __init__(self, config: RememberBrowserConfig) -> None:
        self.config = config

    async def inspect_active_tab(self) -> dict[str, Any]:
        """Probe the user-controlled Remember tab through Chrome DevTools Protocol.

        This is intentionally selector-free. It verifies that we can attach to the
        already-open browser window, find the configured Remember tab, touch the
        DOM, and read a small page summary before real selectors are implemented.
        """
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright") from exc

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(self.config.cdp_url)
            await self._apply_context_overrides(browser.contexts)
            pages = [page for context in browser.contexts for page in context.pages]
            page = self._select_remember_page(pages)
            page_summaries = [{"url": page.url, "title": await page.title()} for page in pages]

            if page is None:
                return {
                    "connected": True,
                    "found": False,
                    "message": "Configured Remember tab was not found.",
                    "pages": page_summaries,
                }

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except PlaywrightTimeoutError:
                pass

            await page.bring_to_front()
            await self._show_demo_overlay(page, "크롤링 테스트 시작")
            marker = await page.evaluate(
                """() => {
                    document.documentElement.dataset.headhuntingProbe = "ok";
                    return document.documentElement.dataset.headhuntingProbe;
                }"""
            )
            await self._apply_page_overrides(page)
            await page.mouse.move(420, 320)
            await page.mouse.wheel(0, 700)
            await page.wait_for_timeout(700)
            await self._show_demo_overlay(page, "스크롤 후 화면 텍스트 수집")
            await page.click("#headhunting-crawl-demo-button", timeout=3000)
            await page.wait_for_timeout(500)
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(700)
            await self._show_demo_overlay(page, "수집 완료")
            snapshot = await page.evaluate(
                """() => {
                    const text = (document.body?.innerText || "").replace(/\\s+/g, " ").trim();
                    const html = document.documentElement?.outerHTML || "";
                    const sample = (selector) => Array.from(document.querySelectorAll(selector))
                        .slice(0, 10)
                        .map((el) => (el.innerText || el.textContent || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "").trim())
                        .filter(Boolean);
                    const visibleTextItems = Array.from(document.querySelectorAll("main *, section *, article *, li, a, button"))
                        .map((el) => (el.innerText || el.textContent || el.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim())
                        .filter((value) => value.length >= 8 && value.length <= 260)
                        .filter((value, index, array) => array.indexOf(value) === index)
                        .slice(0, 24);
                    return {
                        title: document.title,
                        url: location.href,
                        htmlLength: html.length,
                        textLength: text.length,
                        textSample: text.slice(0, 1200),
                        extractedItems: visibleTextItems,
                        linkCount: document.querySelectorAll("a").length,
                        buttonCount: document.querySelectorAll("button").length,
                        inputCount: document.querySelectorAll("input, textarea, select").length,
                        buttonSamples: sample("button"),
                        linkSamples: sample("a"),
                        demoClickCount: window.__headhuntingDemoClicks || 0,
                        scrollY: Math.round(window.scrollY || 0),
                        environment: {
                            userAgent: navigator.userAgent,
                            webdriver: navigator.webdriver,
                            language: navigator.language,
                            languages: navigator.languages,
                            platform: navigator.platform,
                            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
                        }
                    };
                }"""
            )
            return {
                "connected": True,
                "found": True,
                "dom_marker": marker,
                "demo_actions": [
                    "리멤버 창을 앞으로 가져옴",
                    "테스트 오버레이를 페이지에 표시함",
                    "페이지를 아래로 스크롤함",
                    "주입한 테스트 버튼을 클릭함",
                    "현재 화면의 텍스트 후보를 수집함",
                ],
                "pages": page_summaries,
                **snapshot,
            }
        finally:
            await playwright.stop()

    async def search(self, limit: int | None = None) -> list[Candidate]:
        page = await self._active_remember_page()
        if not page:
            raise RuntimeError("Remember tab was not found in the controlled browser.")
        raise NotImplementedError("Remember DOM selectors are pending on an accessible PC.")

    async def open_candidate(self, candidate: Candidate) -> Candidate:
        raise NotImplementedError("Remember candidate detail extraction is pending.")

    async def send_proposal(self, candidate: Candidate) -> tuple[bool, str]:
        raise NotImplementedError("Remember proposal button selectors are pending.")

    async def _active_remember_page(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright") from exc

        playwright = await async_playwright().start()
        browser = await playwright.chromium.connect_over_cdp(self.config.cdp_url)
        for context in browser.contexts:
            for page in context.pages:
                if "remember" in page.url.lower():
                    return page
        return None

    def _select_remember_page(self, pages):
        configured_host = urlparse(self.config.remember_url).netloc.lower().removeprefix("www.")
        for page in pages:
            page_host = urlparse(page.url).netloc.lower().removeprefix("www.")
            if configured_host and configured_host in page_host:
                return page
        for page in pages:
            if "remember" in page.url.lower():
                return page
        return None

    async def _apply_context_overrides(self, contexts) -> None:
        headers = {}
        if self.config.accept_language.strip():
            headers["Accept-Language"] = self.config.accept_language.strip()
        if not headers:
            return
        for context in contexts:
            await context.set_extra_http_headers(headers)

    async def _apply_page_overrides(self, page) -> None:
        session = await page.context.new_cdp_session(page)
        if self.config.locale.strip():
            try:
                await session.send("Emulation.setLocaleOverride", {"locale": self.config.locale.strip()})
            except Exception:
                pass
        if self.config.timezone.strip():
            try:
                await session.send("Emulation.setTimezoneOverride", {"timezoneId": self.config.timezone.strip()})
            except Exception:
                pass

    async def _show_demo_overlay(self, page, message: str) -> None:
        await page.evaluate(
            """(message) => {
                let panel = document.querySelector("#headhunting-crawl-demo-panel");
                if (!panel) {
                    panel = document.createElement("div");
                    panel.id = "headhunting-crawl-demo-panel";
                    panel.style.cssText = [
                        "position:fixed",
                        "right:24px",
                        "top:24px",
                        "z-index:2147483647",
                        "width:280px",
                        "padding:14px",
                        "border-radius:12px",
                        "background:#111827",
                        "color:#fff",
                        "box-shadow:0 16px 40px rgba(0,0,0,.28)",
                        "font:13px/1.55 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif"
                    ].join(";");
                    panel.innerHTML = `
                        <div style="font-weight:800;margin-bottom:6px;">헤드헌팅 크롤링 테스트</div>
                        <div id="headhunting-crawl-demo-message"></div>
                        <button id="headhunting-crawl-demo-button" type="button" style="margin-top:10px;height:32px;border:0;border-radius:8px;background:#2563eb;color:#fff;font-weight:800;padding:0 12px;">테스트 클릭</button>
                    `;
                    document.body.appendChild(panel);
                    window.__headhuntingDemoClicks = 0;
                    panel.querySelector("button").addEventListener("click", () => {
                        window.__headhuntingDemoClicks = (window.__headhuntingDemoClicks || 0) + 1;
                        panel.querySelector("#headhunting-crawl-demo-message").textContent = `테스트 버튼 클릭됨: ${window.__headhuntingDemoClicks}회`;
                    });
                }
                panel.querySelector("#headhunting-crawl-demo-message").textContent = message;
                Array.from(document.querySelectorAll("a, button")).slice(0, 12).forEach((el) => {
                    if (el.id === "headhunting-crawl-demo-button") return;
                    el.style.outline = "2px solid rgba(37,99,235,.55)";
                    el.style.outlineOffset = "2px";
                });
            }""",
            message,
        )
