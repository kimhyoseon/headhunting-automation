from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
        self.results_page_size = 50
        self.last_crawl_result: dict[str, Any] | None = None
        self.progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None

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
            marker = await page.evaluate(
                """() => {
                    document.documentElement.dataset.headhuntingProbe = "ok";
                    return document.documentElement.dataset.headhuntingProbe;
                }"""
            )
            await self._apply_page_overrides(page)
            crawl_result = await self._crawl_current_screen_candidates(page, limit=2)
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
                    "현재 화면에 보이는 인재 카드 탐색",
                    "앞 2명의 인재 카드를 순서대로 클릭",
                    "우측 상세 레이어 또는 열린 카드 영역의 텍스트 수집",
                ],
                "pages": page_summaries,
                **crawl_result,
                **snapshot,
            }
        finally:
            await playwright.stop()

    async def search(self, limit: int | None = None) -> list[Candidate]:
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright") from exc

        crawl_limit = limit if limit and limit > 0 else None
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(self.config.cdp_url)
            await self._apply_context_overrides(browser.contexts)
            pages = [page for context in browser.contexts for page in context.pages]
            page = self._select_remember_page(pages)
            if not page:
                raise RuntimeError("Remember tab was not found in the controlled browser.")

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except PlaywrightTimeoutError:
                pass

            await page.bring_to_front()
            await self._apply_page_overrides(page)
            crawl_result = await self._crawl_candidates_across_pages(page, limit=crawl_limit)
            self.last_crawl_result = crawl_result
            candidates = [
                self._candidate_from_crawl(item)
                for item in crawl_result.get("candidates", [])
                if item.get("success") and item.get("detailText")
            ]
            if not candidates:
                raise RuntimeError("No Remember candidate cards were collected from the current screen.")
            return candidates
        finally:
            await playwright.stop()

    async def open_candidate(self, candidate: Candidate) -> Candidate:
        return candidate

    async def send_proposal(self, candidate: Candidate) -> tuple[bool, str]:
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
            if not page:
                return False, "Remember tab was not found in the controlled browser."

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except PlaywrightTimeoutError:
                pass

            await page.bring_to_front()
            await self._apply_page_overrides(page)

            page_url = candidate.remember_page_url or page.url
            page_number = candidate.remember_page_number or self._page_number_from_url(page_url) or 1
            base_results_url = self._base_results_url(page_url)
            await self._go_to_results_page(page, base_results_url, page_number)

            opened = await self._open_candidate_card_for_sending(page, candidate)
            if not opened.get("ok"):
                return False, str(opened.get("reason") or "candidate card was not found")

            for _ in range(3):
                await page.wait_for_timeout(1200)
                detail = await self._read_visible_detail_layer(page)
                detail_text = str(detail.get("detailText") or "")
                if detail_text and self._detail_matches_candidate(candidate, detail_text):
                    break
            else:
                return False, "candidate detail layer did not match the selected candidate"

            proposal_button = await self._find_proposal_button_in_detail_layer(page)
            if not proposal_button.get("ok"):
                return False, str(proposal_button.get("reason") or "proposal button was not found")

            return True, "DRY RUN: 제안 보내기 버튼 확인 완료(실제 클릭은 주석 처리됨)"
        finally:
            await playwright.stop()

    async def _open_candidate_card_for_sending(self, page, candidate: Candidate) -> dict[str, Any]:
        return await page.evaluate(
            """(candidate) => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const cards = Array.from(document.querySelectorAll('[class*="ResultContainer"]'));
                const cardKey = String(candidate.remember_profile_card_id || "");
                const cardIndex = Number.isInteger(candidate.remember_card_index) ? candidate.remember_card_index : -1;
                const expectedText = clean(candidate.remember_card_text || "");
                const expectedPrefix = expectedText.slice(0, 80);
                const hasCardKey = (candidateCard) => {
                    if (!cardKey) return false;
                    return Array.from(candidateCard.querySelectorAll("[data-highlight-id]"))
                        .some((node) => String(node.getAttribute("data-highlight-id") || "").includes(`profileCard${cardKey}`));
                };
                const cardText = (candidateCard) => clean(candidateCard.innerText || candidateCard.textContent || "");
                let card = cardKey ? cards.find(hasCardKey) : null;
                if (!card && cardIndex >= 0 && cardIndex < cards.length) {
                    const indexedCard = cards[cardIndex];
                    if (!expectedPrefix || cardText(indexedCard).includes(expectedPrefix)) {
                        card = indexedCard;
                    }
                }
                if (!card && expectedPrefix) {
                    card = cards.find((candidateCard) => cardText(candidateCard).includes(expectedPrefix));
                }
                if (!card) {
                    return {
                        ok: false,
                        reason: "candidate card was not found on the Remember results page",
                        cardCount: cards.length,
                        cardKey,
                        cardIndex,
                    };
                }

                card.scrollIntoView({block: "center", inline: "nearest"});
                const forbiddenSelector = [
                    "button",
                    "a",
                    "input",
                    "textarea",
                    "select",
                    "[role='button']",
                    "[aria-live]",
                    "[aria-busy]"
                ].join(",");
                const blockedText = ["Private 포지션", "포지션 뿌리기", "제안 보내기", "제안하기"];
                const isSafeClickContainer = (el) => {
                    if (!el || !card.contains(el)) return false;
                    if (el.closest && el.closest(forbiddenSelector)) return false;
                    const text = clean(el.innerText || el.textContent || "");
                    if (blockedText.some((value) => text.includes(value))) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                let target = null;
                const container = card.querySelector('[class*="SelectableContainer"]') || card;
                if (isSafeClickContainer(container)) {
                    target = container;
                }
                const selectors = [
                    '[class*="InfoContainer"] [class*="textsHighlighter"]',
                    '[class*="InfoContainer"] [class*="JobCategory"]',
                    '[class*="InfoContainer"] span',
                    '[class*="InfoContainer"] div',
                    '[class*="CareerContainer"]',
                    '[class*="InfoContainer"]',
                    '[class*="SelectableContainer"]',
                ];
                if (!target) {
                    for (const selector of selectors) {
                        target = Array.from(card.querySelectorAll(selector)).find(isSafeClickContainer);
                        if (target) break;
                    }
                }
                if (!target) {
                    return {
                        ok: false,
                        reason: "safe candidate click element not found",
                        cardText: cardText(card).slice(0, 1200),
                    };
                }
                target.click();
                return {
                    ok: true,
                    cardIndex: cards.indexOf(card),
                    cardKey,
                    clickedText: clean(target.innerText || target.textContent || "").slice(0, 160),
                    cardText: cardText(card).slice(0, 1200),
                    url: location.href,
                };
            }""",
            candidate.model_dump(mode="json"),
        )

    async def _find_proposal_button_in_detail_layer(self, page) -> dict[str, Any]:
        return await page.evaluate(
            """() => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && rect.bottom > 0
                        && rect.right > 0
                        && rect.top < (window.innerHeight || document.documentElement.clientHeight)
                        && rect.left < (window.innerWidth || document.documentElement.clientWidth)
                        && style.display !== "none"
                        && style.visibility !== "hidden";
                };
                const panels = Array.from(document.querySelectorAll('[data-gnb-open]'))
                    .filter(visible)
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const text = clean(el.innerText || el.textContent || "");
                        const className = String(el.className || "");
                        const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                            .filter((child) => child !== el).length;
                        const isPageShell = el.id === "layout-contents-scrollable"
                            || nestedGnbOpenCount > 0
                            || text.length > 12000
                            || className.includes("LayoutContainer")
                            || className.includes("GnbWrapper")
                            || className.includes("styles__Wrapper")
                            || className.includes("ContentsWrapper");
                        return {el, text, x: rect.x, textLength: text.length, isPageShell};
                    })
                    .filter((item) => !item.isPageShell && item.textLength > 40)
                    .sort((a, b) => b.textLength - a.textLength || b.x - a.x);
                const panel = panels[0]?.el;
                if (!panel) {
                    return {ok: false, reason: "data-gnb-open detail layer not found"};
                }
                const buttons = Array.from(panel.querySelectorAll("button"))
                    .filter(visible)
                    .map((button) => ({
                        button,
                        text: clean(button.innerText || button.textContent || button.getAttribute("aria-label") || ""),
                        disabled: Boolean(button.disabled) || button.getAttribute("aria-disabled") === "true",
                        busy: button.getAttribute("aria-busy") === "true",
                    }))
                    .filter((item) => item.text === "제안 보내기");
                const target = buttons.find((item) => !item.disabled && !item.busy);
                if (!target) {
                    return {
                        ok: false,
                        reason: "enabled proposal button was not found in the detail layer",
                        buttonTexts: buttons.map((item) => item.text),
                    };
                }

                // 실제 발송 연결 시 이 줄만 활성화한다.
                // target.button.click();
                return {
                    ok: true,
                    text: target.text,
                    dryRun: true,
                    buttonCount: buttons.length,
                };
            }"""
        )

    async def _crawl_candidates_across_pages(self, page, limit: int | None) -> dict[str, Any]:
        collected: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        page_results: list[dict[str, Any]] = []
        base_results_url = self._base_results_url(page.url)
        page_number = self._page_number_from_url(base_results_url) or 1
        max_pages = 100 if limit is None else max(1, (limit + 49) // 50 + 3)
        pages_visited = 0
        await self._emit_progress(
            stage="crawling",
            requestedLimit=limit,
            totalCollected=0,
            pageNumber=page_number,
            pageCollected=0,
        )

        while limit is None or len(collected) < limit:
            if pages_visited >= max_pages:
                break
            results_page_url = self._results_page_url(base_results_url, page_number)
            await self._go_to_results_page(page, base_results_url, page_number)
            remaining = 1000 if limit is None else max(limit - len(collected), 0)
            if remaining <= 0:
                break
            page_attempt_limit = self.results_page_size if limit is None else min(remaining, self.results_page_size)

            crawl_result = await self._crawl_current_screen_candidates(
                page,
                limit=page_attempt_limit,
                start_from_top=True,
                page_number=page_number,
                results_page_url=results_page_url,
                progress_offset=len(collected),
                requested_limit=limit,
                target_success_count=remaining,
            )
            page_candidates = crawl_result.get("candidates", [])
            new_count = 0
            for item in page_candidates:
                if not item.get("success") or not item.get("detailText"):
                    continue
                key = self._crawl_identity(item)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                item["order"] = len(collected) + 1
                item["pageNumber"] = page_number
                item["resultsPageUrl"] = results_page_url
                collected.append(item)
                new_count += 1
                await self._emit_progress(
                    stage="crawling",
                    requestedLimit=limit,
                    totalCollected=len(collected),
                    pageNumber=page_number,
                    pageCollected=new_count,
                    currentName=item.get("name", ""),
                )
                if limit is not None and len(collected) >= limit:
                    break

            page_results.append(
                {
                    "pageNumber": page_number,
                    "targetUrl": results_page_url,
                    "url": page.url,
                    "attempted": len(page_candidates),
                    "collected": new_count,
                    "totalCollected": len(collected),
                }
            )
            if limit is not None and len(collected) >= limit:
                break
            if not page_candidates or new_count == 0:
                break
            page_number += 1
            pages_visited += 1

        return {
            "crawlMode": "paginated_candidates",
            "requestedLimit": limit,
            "baseResultsUrl": base_results_url,
            "candidateCount": len(collected),
            "pageCount": len(page_results),
            "pages": page_results,
            "candidates": collected,
        }

    async def _emit_progress(self, **payload: Any) -> None:
        if self.progress_callback:
            await self.progress_callback(payload)

    async def _crawl_current_screen_candidates(
        self,
        page,
        limit: int = 2,
        start_from_top: bool = False,
        page_number: int = 1,
        results_page_url: str | None = None,
        progress_offset: int = 0,
        requested_limit: int | None = None,
        target_success_count: int | None = None,
    ) -> dict[str, Any]:
        if start_from_top:
            await self._reset_candidate_list_to_top(page)
        seen_detail_fingerprints: set[str] = set()
        screen_success_count = 0

        candidate_refs = await page.evaluate(
            """(args) => {
                const limit = args.limit;
                const startFromTop = Boolean(args.startFromTop);
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const profileCardId = (el) => {
                    const values = Array.from(el.querySelectorAll("[data-highlight-id]"))
                        .map((node) => node.getAttribute("data-highlight-id") || "");
                    const match = values.join(" ").match(/profileCard(\\d+)/);
                    return match ? match[1] : "";
                };
                const displayName = (text) => {
                    const blocked = new Set(["조회함", "열람함", "NEW", "최근", "수정됨"]);
                    return clean(text).split(" ")
                        .map((token) => token.replace(/[^가-힣A-Za-zO().-]/g, ""))
                        .find((token) => token && !blocked.has(token) && /^[가-힣A-Za-z][가-힣A-Za-zO().-]{1,11}$/.test(token)) || "";
                };
                const viewportBottom = window.innerHeight || document.documentElement.clientHeight;
                const scroller = document.querySelector("#layout-contents-scrollable");
                const scrollerRect = scroller ? scroller.getBoundingClientRect() : {top: 0, bottom: viewportBottom};
                const cards = Array.from(document.querySelectorAll('[class*="ResultContainer"]'))
                    .map((el, index) => {
                        const rect = el.getBoundingClientRect();
                        const text = clean(el.innerText || el.textContent || "");
                        const intersectsViewport = rect.bottom > scrollerRect.top && rect.top < scrollerRect.bottom;
                        return {
                            index,
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                            text,
                            cardKey: profileCardId(el),
                            visible: intersectsViewport && rect.width > 240 && rect.height > 80 && text.length > 40,
                        };
                    })
                    .filter((card) => card.text.length > 40);
                const visible = cards
                    .filter((card) => card.visible)
                    .sort((a, b) => a.y - b.y || a.index - b.index);
                const visibleIndexes = new Set(visible.map((card) => card.index));
                const firstVisibleIndex = visible.length ? visible[0].index : 0;
                const remaining = cards
                    .filter((card) => !visibleIndexes.has(card.index) && card.index > firstVisibleIndex)
                    .sort((a, b) => a.index - b.index);
                const source = startFromTop
                    ? cards.sort((a, b) => a.index - b.index)
                    : visible.length ? [...visible, ...remaining] : cards.sort((a, b) => a.index - b.index);
                return source.slice(0, limit).map((card) => ({
                    index: card.index,
                    name: displayName(card.text),
                    cardKey: card.cardKey,
                    summaryText: card.text.slice(0, 1200),
                    wasVisible: card.visible,
                }));
            }""",
            {"limit": limit, "startFromTop": start_from_top},
        )

        candidates: list[dict[str, Any]] = []
        for order, ref in enumerate(candidate_refs, start=1):
            before_detail = await page.evaluate(
                """() => {
                    const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                    const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const text = clean(el.innerText || el.textContent || "");
                        const className = String(el.className || "");
                        const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                            .filter((child) => child !== el).length;
                        const isPageShell = el.id === "layout-contents-scrollable"
                            || nestedGnbOpenCount > 0
                            || text.length > 12000
                            || className.includes("LayoutContainer")
                            || className.includes("GnbWrapper")
                            || className.includes("styles__Wrapper");
                        return !isPageShell
                            && text.length > 40
                            && rect.width > 120
                            && rect.height > 80
                            && style.display !== "none"
                            && style.visibility !== "hidden";
                    };
                    const panel = Array.from(document.querySelectorAll('[data-gnb-open]')).find(visible);
                    return {
                        url: location.href,
                        text: panel ? clean(panel.innerText || panel.textContent || "") : "",
                    };
                }"""
            )
            clicked = await page.evaluate(
                """(ref) => {
                    const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                    const cards = Array.from(document.querySelectorAll('[class*="ResultContainer"]'));
                    const cardKey = String(ref.cardKey || "");
                    const hasCardKey = (candidateCard) => {
                        if (!cardKey) return false;
                        return Array.from(candidateCard.querySelectorAll("[data-highlight-id]"))
                            .some((node) => String(node.getAttribute("data-highlight-id") || "").includes(`profileCard${cardKey}`));
                    };
                    const expectedText = clean(ref.summaryText || "");
                    const expectedPrefix = expectedText.slice(0, 80);
                    let card = cardKey ? cards.find(hasCardKey) : null;
                    if (!card) card = cards[ref.index];
                    if (card && expectedPrefix && !clean(card.innerText || card.textContent || "").includes(expectedPrefix)) {
                        card = null;
                    }
                    if (!card && cardKey) {
                        card = cards.find(hasCardKey);
                    }
                    if (!card && expectedPrefix) {
                        card = cards.find((candidateCard) =>
                            clean(candidateCard.innerText || candidateCard.textContent || "").includes(expectedPrefix)
                        );
                    }
                    if (!card) return {ok: false, reason: "candidate card disappeared"};
                    card.scrollIntoView({block: "center", inline: "nearest"});
                    const forbiddenSelector = [
                        "button",
                        "a",
                        "input",
                        "textarea",
                        "select",
                        "[role='button']",
                        "[aria-live]",
                        "[aria-busy]"
                    ].join(",");
                    const blockedText = ["Private 포지션", "포지션 뿌리기", "제안 보내기", "제안하기"];
                    const hasVisibleGnbDetailLayer = () => Array.from(document.querySelectorAll('[data-gnb-open]'))
                        .some((el) => {
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            const text = clean(el.innerText || el.textContent || "");
                            const className = String(el.className || "");
                            const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                                .filter((child) => child !== el).length;
                            const isPageShell = el.id === "layout-contents-scrollable"
                                || nestedGnbOpenCount > 0
                                || text.length > 12000
                                || className.includes("LayoutContainer")
                                || className.includes("GnbWrapper")
                                || className.includes("styles__Wrapper");
                            return !isPageShell
                                && text.length > 40
                                && rect.width > 120
                                && rect.height > 80
                                && style.display !== "none"
                                && style.visibility !== "hidden";
                        });
                    if (card.getAttribute("data-opened") === "true" && hasVisibleGnbDetailLayer()) {
                        return {
                            ok: true,
                            alreadyOpen: true,
                        clickedText: "already-open-card",
                        cardText: clean(card.innerText || card.textContent || "").slice(0, 1200),
                        cardKey,
                    };
                    }
                    const installGuard = () => {
                        if (window.__headhuntingRememberClickGuardInstalled) return;
                        window.__headhuntingRememberClickGuardInstalled = true;
                        document.addEventListener("click", (event) => {
                            const target = event.target && event.target.closest
                                ? event.target.closest("button,a,[role='button'],[aria-live],[aria-busy]")
                                : null;
                            if (!target) return;
                            const text = clean(target.innerText || target.textContent || "");
                            if (!blockedText.some((value) => text.includes(value))) return;
                            event.preventDefault();
                            event.stopImmediatePropagation();
                            window.__headhuntingRememberBlockedClick = text;
                        }, true);
                    };
                    const isSafeTarget = (el) => {
                        if (!el || !card.contains(el)) return false;
                        if (el.closest && el.closest(forbiddenSelector)) return false;
                        const text = clean(el.innerText || el.textContent || "");
                        if (!text || blockedText.some((value) => text.includes(value))) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const isSafeClickContainer = (el) => {
                        if (!el || !card.contains(el)) return false;
                        if (el.closest && el.closest("button,a,input,textarea,select,[aria-live],[aria-busy]")) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    installGuard();
                    let target = null;
                    const container = card.querySelector('[class*="SelectableContainer"]') || card;
                    if (isSafeClickContainer(container)) {
                        target = container;
                    }
                    const selectors = [
                        '[class*="InfoContainer"] [class*="textsHighlighter"]',
                        '[class*="InfoContainer"] [class*="JobCategory"]',
                        '[class*="InfoContainer"] span',
                        '[class*="InfoContainer"] div',
                        '[class*="CareerContainer"]',
                        '[class*="InfoContainer"]',
                        '[class*="SelectableContainer"]',
                    ];
                    if (!target) {
                        for (const selector of selectors) {
                            const matches = Array.from(card.querySelectorAll(selector));
                            target = matches.find(isSafeTarget);
                            if (target) break;
                        }
                    }
                    if (!target) {
                        return {
                            ok: false,
                            reason: "safe candidate click element not found",
                            cardText: clean(card.innerText || card.textContent || "").slice(0, 1200),
                        };
                    }
                    window.__headhuntingRememberBlockedClick = "";
                    target.click();
                    return {
                        ok: true,
                        cardIndex: cards.indexOf(card),
                        clickedText: clean(target.innerText || target.textContent || "").slice(0, 160),
                        cardText: clean(card.innerText || card.textContent || "").slice(0, 1200),
                        cardKey,
                    };
                }""",
                ref,
            )
            if not clicked.get("ok"):
                candidates.append(
                    {
                        "order": order,
                        "cardIndex": clicked.get("cardIndex", ref["index"]),
                        "name": ref.get("name") or f"candidate-{order}",
                        "success": False,
                        "reason": clicked.get("reason", "click target not found"),
                        "summaryText": ref.get("summaryText", ""),
                        "cardKey": ref.get("cardKey", ""),
                        "detailText": "",
                    }
                )
                continue

            await page.wait_for_timeout(1200)
            detail_ready = True
            try:
                await page.wait_for_function(
                    """(before) => {
                        const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                        const panel = Array.from(document.querySelectorAll('[data-gnb-open]'))
                            .find((el) => {
                                const rect = el.getBoundingClientRect();
                                const style = getComputedStyle(el);
                                const text = clean(el.innerText || el.textContent || "");
                                const className = String(el.className || "");
                                const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                                    .filter((child) => child !== el).length;
                                const isPageShell = el.id === "layout-contents-scrollable"
                                    || nestedGnbOpenCount > 0
                                    || text.length > 12000
                                    || className.includes("LayoutContainer")
                                    || className.includes("GnbWrapper")
                                    || className.includes("styles__Wrapper");
                                return !isPageShell
                                    && text.length > 40
                                    && rect.width > 120
                                    && rect.height > 80
                                    && style.display !== "none"
                                    && style.visibility !== "hidden";
                            });
                        if (!panel) return false;
                        if (before && before.allowExisting) return true;
                        const text = clean(panel.innerText || panel.textContent || "");
                        return !before.text || text !== before.text;
                    }""",
                    {
                        "url": before_detail.get("url", ""),
                        "text": before_detail.get("text", ""),
                        "allowExisting": bool(clicked.get("alreadyOpen")),
                    },
                    timeout=3000,
                )
            except Exception:
                pass
            try:
                await page.wait_for_function(
                    """(args) => {
                        const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                        const profileId = new URL(location.href).searchParams.get("profileId") || "";
                        const tokenSet = (value) => {
                            const blocked = new Set([
                                "조회함", "열람함", "남성", "여성", "상태", "선택", "제안", "보내기",
                                "경력", "학력", "현재", "근무", "이직", "유사", "인재", "보기",
                                "마지막", "접속일", "최근", "수정됨", "적극", "구직", "NEW",
                                "정보보안", "개인정보보호", "정보보호", "보안", "보안정책", "감사",
                                "컴플라이언스", "ISMS", "프라이버시", "SW개발", "엔지니어"
                            ]);
                            return new Set(clean(value).split(/\\s+/)
                                .map((token) => token.replace(/^[^가-힣A-Za-z0-9(]+|[^가-힣A-Za-z0-9)]+$/g, ""))
                                .filter((token) => token.length >= 3)
                                .filter((token) => !token.includes("OO"))
                                .filter((token) => !/^\\d/.test(token))
                                .filter((token) => !/년생|개월|\\d{4}\\.\\d{2}/.test(token))
                                .filter((token) => !blocked.has(token)));
                        };
                        const matchesCard = (cardText, detailText) => {
                            const cardTokens = tokenSet(cardText);
                            if (!cardTokens.size) return clean(detailText).length > 80;
                            const detailTokens = tokenSet(detailText);
                            let matches = 0;
                            for (const token of cardTokens) {
                                if (detailTokens.has(token) || clean(detailText).includes(token)) matches += 1;
                                if (matches >= 2) return true;
                            }
                            return false;
                        };
                        const panel = Array.from(document.querySelectorAll('[data-gnb-open]'))
                            .find((el) => {
                                const rect = el.getBoundingClientRect();
                                const style = getComputedStyle(el);
                                const text = clean(el.innerText || el.textContent || "");
                                const className = String(el.className || "");
                                const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                                    .filter((child) => child !== el).length;
                                const isPageShell = el.id === "layout-contents-scrollable"
                                    || nestedGnbOpenCount > 0
                                    || text.length > 12000
                                    || className.includes("LayoutContainer")
                                    || className.includes("GnbWrapper")
                                    || className.includes("styles__Wrapper")
                                    || className.includes("ContentsWrapper");
                                return !isPageShell
                                    && text.length > 40
                                    && rect.width > 120
                                    && rect.height > 80
                                    && style.display !== "none"
                                    && style.visibility !== "hidden";
                            });
                        if (!panel) return false;
                        const text = clean(panel.innerText || panel.textContent || "");
                        if (!args.allowExisting && args.beforeText && text === args.beforeText) return false;
                        if (args.cardKey && profileId && profileId !== String(args.cardKey)) return false;
                        return matchesCard(args.cardText || "", text);
                    }""",
                    {
                        "beforeText": before_detail.get("text", ""),
                        "cardText": clicked.get("cardText", ref.get("summaryText", "")),
                        "cardKey": clicked.get("cardKey") or ref.get("cardKey", ""),
                        "allowExisting": bool(clicked.get("alreadyOpen")),
                    },
                    timeout=8000,
                )
            except Exception:
                detail_ready = False
            try:
                await page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('[data-gnb-open]'))
                        .some((el) => {
                            const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            const text = clean(el.innerText || el.textContent || "");
                            const className = String(el.className || "");
                            const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                                .filter((child) => child !== el).length;
                            const isPageShell = el.id === "layout-contents-scrollable"
                                || nestedGnbOpenCount > 0
                                || text.length > 12000
                                || className.includes("LayoutContainer")
                                || className.includes("GnbWrapper")
                                || className.includes("styles__Wrapper");
                            return !isPageShell
                                && text.length > 40
                                && rect.width > 120
                                && rect.height > 80
                                && style.display !== "none"
                                && style.visibility !== "hidden";
                        })""",
                    timeout=3000,
                )
            except Exception:
                pass
            blocked_click = await page.evaluate("""() => window.__headhuntingRememberBlockedClick || ''""")
            if blocked_click:
                candidates.append(
                    {
                        "order": order,
                        "cardIndex": clicked.get("cardIndex", ref["index"]),
                        "name": ref.get("name") or f"candidate-{order}",
                        "success": False,
                        "wasVisible": ref.get("wasVisible", False),
                        "reason": f"blocked unsafe button click: {blocked_click}",
                        "summaryText": ref.get("summaryText", ""),
                        "cardKey": ref.get("cardKey", ""),
                        "detailText": "",
                        "detailLength": 0,
                        "detailSource": "blocked",
                        "panelCount": 0,
                        "url": page.url,
                    }
                )
                continue
            detail = await page.evaluate(
                """(args) => {
                    const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                    const profileText = (el) => (el.innerText || el.textContent || "")
                        .replace(/\\r/g, "")
                        .replace(/[ \\t]+\\n/g, "\\n")
                        .replace(/\\n[ \\t]+/g, "\\n")
                        .replace(/\\n{3,}/g, "\\n\\n")
                        .trim();
                    const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 120
                            && rect.height > 80
                            && rect.bottom > 0
                            && rect.right > 0
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight)
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth)
                            && style.display !== "none"
                            && style.visibility !== "hidden";
                    };
                    const panelSelectors = [
                        '[data-gnb-open]',
                        '[role="dialog"]',
                        'aside',
                        '[class*="Drawer"]',
                        '[class*="Modal"]',
                        '[class*="Layer"]',
                        '[data-opened="true"][class*="ResultContainer"]'
                    ];
                    const panels = panelSelectors
                        .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                        .filter((el, index, array) => array.indexOf(el) === index)
                        .filter(visible)
                        .map((el) => {
                            const rect = el.getBoundingClientRect();
                            const text = profileText(el);
                            const plainText = clean(text);
                            const className = String(el.className || "");
                            const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                                .filter((child) => child !== el).length;
                            const isGnbLayer = el.matches('[data-gnb-open]');
                            const isPageShell = isGnbLayer && (
                                el.id === "layout-contents-scrollable"
                                || nestedGnbOpenCount > 0
                                || plainText.length > 12000
                                || className.includes("LayoutContainer")
                                || className.includes("GnbWrapper")
                                || className.includes("styles__Wrapper")
                            );
                            return {
                                source: isGnbLayer ? "gnb-detail-layer"
                                    : el.matches('[role="dialog"]') ? "dialog"
                                    : el.matches("aside") ? "aside"
                                    : el.matches('[data-opened="true"][class*="ResultContainer"]') ? "opened-card"
                                    : "panel",
                                isPageShell,
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height),
                                text,
                                plainText,
                            };
                        })
                        .filter((item) => !item.isPageShell)
                        .filter((item) => item.plainText.length > 40)
                        .sort((a, b) => {
                            const sourceRank = (value) => {
                                if (value === "gnb-detail-layer") return 0;
                                if (value === "dialog" || value === "aside" || value === "panel") return 1;
                                if (value === "opened-card") return 3;
                                return 3;
                            };
                            return sourceRank(a.source) - sourceRank(b.source)
                                || b.plainText.length - a.plainText.length
                                || b.x - a.x;
                        });
                    const expectedPrefix = clean(args.cardText || "").slice(0, 24);
                    const gnbPanels = panels.filter((item) => item.source === "gnb-detail-layer");
                    const matchingPanel = gnbPanels.find((item) =>
                        (args.name && item.plainText.includes(args.name)) ||
                        (expectedPrefix && item.plainText.includes(expectedPrefix))
                    );
                    const selected = matchingPanel || gnbPanels[0];
                    if (!selected) {
                        return {
                            detailSource: "missing-gnb-detail-layer",
                            detailText: "",
                            detailLength: 0,
                            panelCount: panels.length,
                            gnbPanelCount: 0,
                            reason: "data-gnb-open detail layer not found",
                            url: location.href,
                        };
                    }
                    const selectedPlainText = clean(selected.text);
                    if (!args.allowExisting && selectedPlainText === args.beforeText) {
                        return {
                            detailSource: "stale-gnb-detail-layer",
                            detailText: "",
                            detailLength: 0,
                            panelCount: panels.length,
                            gnbPanelCount: gnbPanels.length,
                            reason: "candidate click did not update the data-gnb-open detail layer",
                            url: location.href,
                        };
                    }
                    return {
                        detailSource: selected.source,
                        detailText: selected.text.slice(0, 6000),
                        detailLength: selected.text.length,
                        panelCount: panels.length,
                        gnbPanelCount: gnbPanels.length,
                        url: location.href,
                    };
                }""",
                {
                    "cardText": clicked.get("cardText", ""),
                    "name": ref.get("name", ""),
                    "beforeText": before_detail.get("text", ""),
                    "beforeUrl": before_detail.get("url", ""),
                    "allowExisting": bool(clicked.get("alreadyOpen")),
                },
            )
            raw_detail_text = str(detail.get("detailText", "") or "")
            cleaned_detail_text = self._clean_profile_text(raw_detail_text)
            cleaned_success = bool(cleaned_detail_text and cleaned_detail_text != "-")
            detail_reason = detail.get("reason", "")
            card_matches_detail = self._detail_matches_card(
                str(clicked.get("cardText") or ref.get("summaryText") or ""),
                raw_detail_text,
            )
            if raw_detail_text and not cleaned_detail_text:
                detail_reason = "profile detail text became empty after cleanup"
            if cleaned_detail_text == "-":
                detail_reason = "profile detail text is empty"
            if cleaned_success and not card_matches_detail:
                cleaned_success = False
                detail_reason = "detail layer did not match the clicked candidate card"
            detail_fingerprint = self._detail_fingerprint(cleaned_detail_text)
            if cleaned_success and detail_fingerprint and detail_fingerprint in seen_detail_fingerprints:
                cleaned_success = False
                detail_reason = "detail layer still shows a previously collected candidate"
            if not cleaned_success:
                for _ in range(3):
                    await page.wait_for_timeout(2500)
                    retry_detail = await self._read_visible_detail_layer(page)
                    retry_raw_detail_text = str(retry_detail.get("detailText", "") or "")
                    retry_cleaned_detail_text = self._clean_profile_text(retry_raw_detail_text)
                    retry_fingerprint = self._detail_fingerprint(retry_cleaned_detail_text)
                    retry_matches_detail = self._detail_matches_card(
                        str(clicked.get("cardText") or ref.get("summaryText") or ""),
                        retry_raw_detail_text,
                    )
                    retry_success = bool(retry_cleaned_detail_text and retry_cleaned_detail_text != "-")
                    if retry_success and retry_matches_detail and (
                        not retry_fingerprint or retry_fingerprint not in seen_detail_fingerprints
                    ):
                        detail = retry_detail
                        raw_detail_text = retry_raw_detail_text
                        cleaned_detail_text = retry_cleaned_detail_text
                        detail_fingerprint = retry_fingerprint
                        cleaned_success = True
                        detail_reason = ""
                        card_matches_detail = True
                        break
            if cleaned_success:
                if detail_fingerprint:
                    seen_detail_fingerprints.add(detail_fingerprint)
                screen_success_count += 1
                await self._emit_progress(
                    stage="crawling",
                    requestedLimit=requested_limit,
                    crawledCount=min(progress_offset + screen_success_count, requested_limit)
                    if requested_limit
                    else progress_offset + screen_success_count,
                    pageNumber=page_number,
                    pageCollected=screen_success_count,
                    currentName=self._candidate_name_from_crawl(ref, cleaned_detail_text or raw_detail_text, order),
                )
            should_stop_for_target = (
                target_success_count is not None
                and target_success_count > 0
                and screen_success_count >= target_success_count
            )
            candidates.append(
                {
                    "order": order,
                    "cardIndex": clicked.get("cardIndex", ref["index"]),
                    "name": self._candidate_name_from_crawl(ref, cleaned_detail_text or raw_detail_text, order),
                    "success": cleaned_success,
                    "wasVisible": ref.get("wasVisible", False),
                    "summaryText": ref.get("summaryText", ""),
                    "cardKey": clicked.get("cardKey") or ref.get("cardKey", ""),
                    "cardText": clicked.get("cardText", ref.get("summaryText", "")),
                    "detailText": cleaned_detail_text,
                    "detailFingerprint": detail_fingerprint,
                    "detailLength": len(cleaned_detail_text),
                    "rawDetailLength": detail.get("detailLength", 0),
                    "detailSource": detail.get("detailSource", "unknown"),
                    "panelCount": detail.get("panelCount", 0),
                    "gnbPanelCount": detail.get("gnbPanelCount", 0),
                    "reason": detail_reason,
                    "url": detail.get("url", page.url),
                    "resultsPageUrl": results_page_url or page.url,
                    "profileId": self._profile_id_from_url(str(detail.get("url", page.url) or "")),
                }
            )
            if should_stop_for_target:
                break

        return {
            "crawlMode": "current_screen_candidates",
            "pageNumber": page_number,
            "startFromTop": start_from_top,
            "requestedLimit": limit,
            "candidateCount": len(candidates),
            "candidates": candidates,
        }

    async def _read_visible_detail_layer(self, page) -> dict[str, Any]:
        return await page.evaluate(
            """() => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const profileText = (el) => (el.innerText || el.textContent || "")
                    .replace(/\\r/g, "")
                    .replace(/[ \\t]+\\n/g, "\\n")
                    .replace(/\\n[ \\t]+/g, "\\n")
                    .replace(/\\n{3,}/g, "\\n\\n")
                    .trim();
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 120
                        && rect.height > 80
                        && rect.bottom > 0
                        && rect.right > 0
                        && rect.top < (window.innerHeight || document.documentElement.clientHeight)
                        && rect.left < (window.innerWidth || document.documentElement.clientWidth)
                        && style.display !== "none"
                        && style.visibility !== "hidden";
                };
                const panels = Array.from(document.querySelectorAll('[data-gnb-open]'))
                    .filter(visible)
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const text = profileText(el);
                        const plainText = clean(text);
                        const className = String(el.className || "");
                        const nestedGnbOpenCount = Array.from(el.querySelectorAll('[data-gnb-open]'))
                            .filter((child) => child !== el).length;
                        const isPageShell = el.id === "layout-contents-scrollable"
                            || nestedGnbOpenCount > 0
                            || plainText.length > 12000
                            || className.includes("LayoutContainer")
                            || className.includes("GnbWrapper")
                            || className.includes("styles__Wrapper")
                            || className.includes("ContentsWrapper");
                        return {
                            isPageShell,
                            source: "gnb-detail-layer",
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                            text,
                            plainText,
                        };
                    })
                    .filter((item) => !item.isPageShell)
                    .filter((item) => item.plainText.length > 40)
                    .sort((a, b) => b.plainText.length - a.plainText.length || b.x - a.x);
                const selected = panels[0];
                if (!selected) {
                    return {
                        detailSource: "missing-gnb-detail-layer",
                        detailText: "",
                        detailLength: 0,
                        panelCount: panels.length,
                        gnbPanelCount: 0,
                        reason: "data-gnb-open detail layer not found",
                        url: location.href,
                    };
                }
                return {
                    detailSource: selected.source,
                    detailText: selected.text.slice(0, 6000),
                    detailLength: selected.text.length,
                    panelCount: panels.length,
                    gnbPanelCount: panels.length,
                    url: location.href,
                };
            }"""
        )

    async def _reset_candidate_list_to_top(self, page) -> None:
        await page.evaluate(
            """() => {
                const scrollToTop = (el) => {
                    if (!el) return false;
                    if (typeof el.scrollTo === "function") {
                        el.scrollTo({top: 0, left: el.scrollLeft || 0, behavior: "instant"});
                    } else {
                        el.scrollTop = 0;
                    }
                    return true;
                };
                const cards = Array.from(document.querySelectorAll('[class*="ResultContainer"]'));
                const scrollers = new Set();
                const addScrollableAncestors = (el) => {
                    let current = el;
                    while (current && current !== document.body && current !== document.documentElement) {
                        const style = getComputedStyle(current);
                        const canScroll = current.scrollHeight > current.clientHeight + 20;
                        const overflowY = `${style.overflowY} ${style.overflow}`;
                        if (canScroll && /(auto|scroll|overlay)/.test(overflowY)) {
                            scrollers.add(current);
                        }
                        current = current.parentElement;
                    }
                };
                cards.slice(0, 3).forEach(addScrollableAncestors);
                const layoutScroller = document.querySelector("#layout-contents-scrollable");
                if (layoutScroller) scrollers.add(layoutScroller);
                scrollers.forEach(scrollToTop);
                scrollToTop(document.scrollingElement || document.documentElement);
                window.scrollTo({top: 0, left: window.scrollX || 0, behavior: "instant"});
                return {scrollerCount: scrollers.size};
            }"""
        )
        await page.wait_for_timeout(700)

    async def _go_to_results_page(self, page, base_results_url: str, page_number: int) -> None:
        target_url = self._results_page_url(base_results_url, page_number)
        if target_url != page.url:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_function(
                """() => document.querySelectorAll('[class*="ResultContainer"]').length > 0
                    || /검색 결과|결과가|없습니다|No results/i.test(document.body?.innerText || "")""",
                timeout=12000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(800)

    @staticmethod
    def _base_results_url(current_url: str) -> str:
        parsed = urlparse(current_url)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        skipped = {"profileId", "profileSource"}
        base_params = [(key, value) for key, value in params if key not in skipped]
        return urlunparse(parsed._replace(query=urlencode(base_params, doseq=True)))

    @staticmethod
    def _results_page_url(base_results_url: str, page_number: int) -> str:
        parsed = urlparse(base_results_url)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        next_params: list[tuple[str, str]] = []
        page_set = False
        for key, value in params:
            if key == "page":
                next_params.append((key, str(page_number)))
                page_set = True
            else:
                next_params.append((key, value))
        if not page_set:
            next_params.append(("page", str(page_number)))
        return urlunparse(parsed._replace(query=urlencode(next_params, doseq=True)))

    @staticmethod
    def _page_number_from_url(current_url: str) -> int | None:
        query = dict(parse_qsl(urlparse(current_url).query, keep_blank_values=True))
        try:
            value = int(query.get("page", "") or "0")
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _crawl_identity(item: dict[str, Any]) -> str:
        detail_fingerprint = str(item.get("detailFingerprint") or "")
        if not detail_fingerprint:
            detail_fingerprint = BrowserRememberAdapter._detail_fingerprint(str(item.get("detailText") or ""))
        if detail_fingerprint:
            return f"detail:{detail_fingerprint}"
        profile_card_id = str(item.get("cardKey") or "")
        if profile_card_id:
            return f"profile-card:{profile_card_id}"
        text = str(item.get("detailText") or item.get("summaryText") or "").strip()
        compact = " ".join(text.split())
        if compact:
            return "text:" + compact[:1000]
        url = str(item.get("url") or "")
        query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
        profile_id = query.get("profileId")
        if profile_id:
            return f"profile:{profile_id}"
        return f"candidate:{item.get('pageNumber', '')}:{item.get('cardIndex', '')}:{item.get('name', '')}"

    @staticmethod
    def _detail_fingerprint(text: str) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(compact) < 80:
            return ""
        return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _candidate_name_from_crawl(cls, ref: dict[str, Any], detail_text: str, order: int) -> str:
        return (
            cls._guess_candidate_name(detail_text)
            or cls._clean_card_candidate_name(str(ref.get("name") or ""))
            or f"후보자{order}"
        )

    @staticmethod
    def _profile_id_from_url(value: str) -> str | None:
        query = dict(parse_qsl(urlparse(value).query, keep_blank_values=True))
        return query.get("profileId") or None

    @classmethod
    def _guess_candidate_name(cls, text: str) -> str:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if cls._looks_like_candidate_name(line):
                return line
        compact = " ".join(text.split())
        if not compact:
            return ""
        return cls._clean_card_candidate_name(compact.split(" ", 1)[0])

    @staticmethod
    def _clean_card_candidate_name(name: str) -> str:
        cleaned = re.sub(r"[^\w가-힣()·.-]", "", name.strip())
        blocked = {"조회함", "열람함", "조회", "리멤버", "후보자", "Private", "NEW"}
        if not cleaned or cleaned in blocked:
            return ""
        if re.search(r"\d", cleaned):
            return ""
        return cleaned if len(cleaned) <= 12 else ""

    @classmethod
    def _looks_like_candidate_name(cls, line: str) -> bool:
        cleaned = cls._clean_card_candidate_name(line)
        if not cleaned:
            return False
        blocked = {
            "남성",
            "여성",
            "자기소개",
            "자기소개서",
            "전문분야·스킬",
            "전문분야스킬",
            "경력사항",
            "학력사항",
            "자격증",
        }
        if cleaned.replace(" ", "") in blocked:
            return False
        return bool(re.match(r"^[가-힣A-Za-z][가-힣A-Za-zO()·.-]{1,11}$", cleaned))

    @staticmethod
    def _clean_profile_text(text: str) -> str:
        if not text:
            return ""

        major_headers = [
            "자기소개",
            "자기소개서",
            "전문 분야·스킬",
            "리멤버 분석 태그",
            "경력사항",
            "학력사항",
            "자격증",
            "외국어",
            "수상",
            "병역",
        ]
        terminal_headers = {
            "이력서",
            "포트폴리오",
            "전형 히스토리",
            "소싱 히스토리",
            "메모 히스토리",
        }
        skip_exact = {
            "리멤버",
            "링크드인",
            "DART",
            "프로필 이력서로 변환",
            "프로필 정보",
            "파일 관리",
            "+ 메모 추가",
            "+ 이력서 추가",
            "+ 파일 추가",
            "직접 등록 태그",
            "유사 경력",
            "상태 선택",
            "제안 보내기",
        }

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if normalized.count("\n") < 5:
            markers = major_headers + list(terminal_headers)
            for marker in markers:
                normalized = re.sub(rf"\s+({re.escape(marker)})(?=\s|$)", rf"\n{marker}\n", normalized)
            normalized = re.sub(r"\s+(최종 업데이트\s+\d{4}/\d{2}/\d{2})", r"\n\1\n", normalized)
            normalized = re.sub(r"\s+(마지막 접속일\s+(?:\d{4}/\d{2}/\d{2}|Invalid Date))", r"\n\1\n", normalized)
            normalized = re.sub(r"\s+(프로필 이력서로 변환)", r"\n\1\n", normalized)

        kept: list[str] = []
        for raw_line in normalized.split("\n"):
            line = re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
            if not line:
                continue
            if line in terminal_headers:
                break
            if line in skip_exact:
                continue
            if re.match(r"^최종 업데이트\b", line):
                continue
            if re.match(r"^마지막 접속일\b", line):
                continue
            if re.match(r"^(전형 히스토리|소싱 히스토리)\s*\d+$", line):
                continue
            if re.match(r"^메모\s*\d+$", line):
                continue
            if re.match(r"^(전체|추천|원본|기본|유의 사항)\s*\d+$", line):
                continue
            if re.match(r"^\d+\s*/\s*\d+$", line):
                continue
            if re.match(r"^등록된 .+(?:이|가) 없습니다$", line):
                continue
            if re.match(r"^.+ 히스토리가 없습니다$", line):
                continue
            if line == "메모 내역이 없습니다":
                continue
            kept.append(line)

        formatted: list[str] = []
        major_header_set = set(major_headers)
        for line in kept:
            if line in major_header_set and formatted and formatted[-1] != "":
                formatted.append("")
            formatted.append(line)

        cleaned_lines: list[str] = []
        for line in formatted:
            if line == "" and (not cleaned_lines or cleaned_lines[-1] == ""):
                continue
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    @classmethod
    def _detail_matches_card(cls, card_text: str, detail_text: str) -> bool:
        card_tokens = cls._distinctive_card_tokens(card_text)
        if not card_tokens:
            return len(" ".join(detail_text.split())) > 80
        compact_detail = " ".join(detail_text.split())
        detail_tokens = cls._distinctive_card_tokens(detail_text)
        matches = 0
        for token in card_tokens:
            if token in detail_tokens or token in compact_detail:
                matches += 1
            if matches >= 2:
                return True
        return False

    @classmethod
    def _detail_matches_candidate(cls, candidate: Candidate, detail_text: str) -> bool:
        card_text = str(getattr(candidate, "remember_card_text", "") or "")
        if card_text and cls._detail_matches_card(card_text, detail_text):
            return True

        detail_compact = " ".join(str(detail_text or "").split())
        matches = 0
        for value in [
            str(getattr(candidate, "name", "") or ""),
            str(getattr(candidate, "company", "") or ""),
            str(getattr(candidate, "role", "") or ""),
        ]:
            value = " ".join(value.split())
            if value and value != "-" and value in detail_compact:
                matches += 1
        return matches >= 1

    @staticmethod
    def _distinctive_card_tokens(text: str) -> set[str]:
        blocked = {
            "조회함",
            "열람함",
            "남성",
            "여성",
            "상태",
            "선택",
            "제안",
            "보내기",
            "경력",
            "학력",
            "현재",
            "근무",
            "이직",
            "유사",
            "인재",
            "보기",
            "마지막",
            "접속일",
            "최근",
            "수정됨",
            "적극",
            "구직",
            "NEW",
            "정보보안",
            "개인정보보호",
            "정보보호",
            "보안",
            "보안정책",
            "감사",
            "컴플라이언스",
            "ISMS",
            "프라이버시",
            "SW개발",
            "엔지니어",
        }
        tokens = set()
        for raw_token in re.split(r"\s+", " ".join(text.split())):
            token = re.sub(r"^[^가-힣A-Za-z0-9(]+|[^가-힣A-Za-z0-9)]+$", "", raw_token)
            if len(token) < 3:
                continue
            if "OO" in token or token in blocked:
                continue
            if re.match(r"^\d", token):
                continue
            if re.search(r"년생|개월|\d{4}\.\d{2}", token):
                continue
            tokens.add(token)
        return tokens

    def _candidate_from_crawl(self, item: dict[str, Any]) -> Candidate:
        order = int(item.get("order") or 0)
        text = self._clean_profile_text(str(item.get("detailText") or item.get("summaryText") or "").strip())
        compact = " ".join(text.split())
        profile = self._profile_metadata(text)
        name = str(profile.get("name") or item.get("name") or self._guess_candidate_name(text) or f"후보자{order}").strip()
        tokens = compact.split()
        role = profile.get("role") or self._first_matching_text(tokens, ["개발", "엔지니어", "PM", "PO", "디자이너", "마케터", "영업", "기획"]) or "-"
        company = profile.get("company") or "-"
        experience = profile.get("experience") or "-"
        location = profile.get("location") or "-"
        skills = self._skill_tokens(tokens)
        detail_url = str(item.get("url") or "")
        page_url = str(item.get("resultsPageUrl") or "")
        profile_id = str(item.get("profileId") or self._profile_id_from_url(detail_url) or "")
        profile_card_id = str(item.get("cardKey") or "")
        return Candidate(
            id=f"remember-{order:02d}-{name}",
            name=name,
            company=company,
            role=role,
            experience=experience,
            location=location,
            skills=skills,
            resume_text=text,
            remember_page_number=int(item.get("pageNumber") or 0) or None,
            remember_page_url=page_url or None,
            remember_detail_url=detail_url or None,
            remember_profile_id=profile_id or None,
            remember_profile_card_id=profile_card_id or None,
            remember_card_index=int(item.get("cardIndex") or 0) if item.get("cardIndex") is not None else None,
            remember_card_text=str(item.get("cardText") or item.get("summaryText") or "")[:1200] or None,
        )

    @classmethod
    def _profile_metadata(cls, text: str) -> dict[str, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return {}

        name = cls._guess_candidate_name(text)
        role = cls._extract_skill_role(lines)
        company, career_role = cls._extract_current_career(lines)
        experience = cls._extract_experience(lines)
        location = cls._extract_location(lines)
        return {
            "name": name,
            "company": company,
            "role": role or career_role,
            "experience": experience,
            "location": location,
        }

    @classmethod
    def _extract_skill_role(cls, lines: list[str]) -> str:
        section = cls._section_lines(lines, "전문 분야·스킬", {"리멤버 분석 태그", "경력사항", "학력사항"})
        values = []
        for line in section:
            if cls._is_profile_noise_line(line) or line.startswith("#"):
                continue
            values.append(line.strip("·,"))
            if len(values) >= 3:
                break
        return ", ".join(values)

    @classmethod
    def _extract_current_career(cls, lines: list[str]) -> tuple[str, str]:
        section = cls._section_lines(lines, "경력사항", {"학력사항", "자격증", "외국어", "수상", "병역"})
        company = ""
        role = ""
        for line in section:
            if cls._is_career_skip_line(line):
                continue
            company = line.strip("·,")
            break
        if company:
            try:
                start = section.index(company)
            except ValueError:
                start = -1
            for line in section[start + 1 : start + 5]:
                if cls._is_career_skip_line(line):
                    continue
                if re.search(r"\d{4}\.\d{2}|현재|\d+개월|\d+년", line):
                    continue
                role = line.strip("·,")
                break
        return company, role

    @staticmethod
    def _extract_experience(lines: list[str]) -> str:
        for line in lines:
            value = line.strip("() ")
            if "년차" in value or ("총" in value and "근무" in value):
                return value
        for line in lines:
            if re.search(r"경력\s*\d+\s*년", line):
                return line
        return ""

    @staticmethod
    def _extract_location(lines: list[str]) -> str:
        regions = {
            "서울",
            "서울특별시",
            "경기",
            "경기도",
            "인천",
            "인천광역시",
            "부산",
            "부산광역시",
            "대전",
            "대전광역시",
            "대구",
            "대구광역시",
            "광주",
            "광주광역시",
            "울산",
            "울산광역시",
            "세종",
            "세종특별자치시",
            "강원",
            "충북",
            "충남",
            "전북",
            "전남",
            "경북",
            "경남",
            "제주",
        }
        for line in lines:
            stripped = line.strip("·, ")
            if stripped in regions:
                return stripped
        return ""

    @classmethod
    def _section_lines(cls, lines: list[str], start_header: str, end_headers: set[str]) -> list[str]:
        try:
            start = lines.index(start_header) + 1
        except ValueError:
            return []
        result = []
        for line in lines[start:]:
            if line in end_headers:
                break
            result.append(line)
        return result

    @classmethod
    def _is_profile_noise_line(cls, line: str) -> bool:
        if line in {"남성", "여성", "재직중", "전직장", "현직장"}:
            return True
        if re.match(r"^\d{4}년생(?:\(추정\))?$", line):
            return True
        if re.match(r"^만\s*\d+세", line):
            return True
        return False

    @classmethod
    def _is_career_skip_line(cls, line: str) -> bool:
        if cls._is_profile_noise_line(line):
            return True
        if not line or line.startswith("#"):
            return True
        if line in {"공백기", "이직", "재직중", "전직장", "현직장"}:
            return True
        if re.match(r"^\(?\d+년차", line) or ("총" in line and "근무" in line):
            return True
        if re.match(r"^\d+\s*년\s*이직$", line):
            return True
        if re.match(r"^\d{4}\.\d{2}\s*~", line):
            return True
        if re.match(r"^\(?\d+년|\(?\d+개월", line):
            return True
        return False

    @staticmethod
    def _first_matching_text(values: list[str], needles: list[str]) -> str:
        for value in values:
            if any(needle.lower() in value.lower() for needle in needles):
                return value
        return ""

    @staticmethod
    def _first_company_like(values: list[str]) -> str:
        skip = {"남성", "여성", "재직중", "전직장", "현직장"}
        for value in values:
            cleaned = value.strip("·,")
            if not cleaned or cleaned in skip:
                continue
            if any(marker in cleaned for marker in ["회사", "주식회사", "랩스", "테크", "코리아", "Inc", "Corp"]):
                return cleaned
        for value in values:
            cleaned = value.strip("·,")
            if cleaned and cleaned not in skip:
                return cleaned
        return ""

    @staticmethod
    def _skill_tokens(values: list[str]) -> list[str]:
        keywords = []
        seen = set()
        for value in values:
            cleaned = value.strip("·,/#()[]")
            if len(cleaned) < 2 or len(cleaned) > 24:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            if any(marker in lowered for marker in ["python", "java", "react", "figma", "sql", "aws", "pm", "po", "saas", "b2b", "기획", "개발", "디자인", "마케팅", "영업"]):
                seen.add(lowered)
                keywords.append(cleaned)
            if len(keywords) >= 8:
                break
        return keywords

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
