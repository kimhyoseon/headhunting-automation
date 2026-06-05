from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.models import Candidate


@dataclass(slots=True)
class SaraminBrowserConfig:
    cdp_url: str = "http://127.0.0.1:9232"
    saramin_url: str = "https://www.saramin.co.kr/zf_user/memcom/talent-pool/main/search"
    locale: str = "ko-KR"
    accept_language: str = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    timezone: str = "Asia/Seoul"
    skip_proposal_send: bool = True
    offer_position: dict[str, str] | None = None


class BrowserSaraminAdapter:
    def __init__(self, config: SaraminBrowserConfig) -> None:
        self.display_name = "browser Saramin adapter"
        self.provider_name = "Saramin"
        self.config = config
        self.results_page_size = 20
        self.last_crawl_result: dict[str, Any] | None = None
        self.progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def reset_cancel(self) -> None:
        self._cancel_requested = False

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise asyncio.CancelledError()

    async def inspect_search_result_count(self) -> dict[str, Any]:
        page = await self._with_saramin_page()
        try:
            refs = await self._candidate_refs(page)
            return {
                "ok": bool(refs),
                "count": None,
                "visibleCount": len(refs),
                "url": page.url,
                "reason": "" if refs else "Saramin search candidates were not found on the current page.",
            }
        finally:
            await page.context.browser.close()

    async def inspect_offer_positions(self) -> dict[str, Any]:
        page = await self._with_saramin_page()
        detail = None
        try:
            detail = await self._open_first_candidate_detail(page)
            await self._open_offer_modal(detail)
            options = await self._read_offer_position_options(detail)
            return {
                "ok": bool(options),
                "positions": options,
                "candidateUrl": detail.url,
                "reason": "" if options else "No Saramin offer positions were found.",
            }
        finally:
            if detail and not detail.is_closed():
                await detail.close()
            await page.context.browser.close()

    async def search(self, limit: int | None = None) -> list[Candidate]:
        self._raise_if_cancelled()
        page = await self._with_saramin_page()
        collected: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        page_summaries: list[dict[str, Any]] = []
        requested = limit if limit and limit > 0 else None
        max_pages = 100 if requested is None else max(1, (requested + self.results_page_size - 1) // self.results_page_size + 3)
        page_number = 1

        try:
            await self._emit_progress(
                stage="crawling",
                requestedLimit=requested,
                totalCollected=0,
                pageNumber=page_number,
                pageCollected=0,
            )
            while requested is None or len(collected) < requested:
                self._raise_if_cancelled()
                if page_number > max_pages:
                    break
                await page.bring_to_front()
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                refs = await self._candidate_refs(page)
                if not refs:
                    break
                page_new = 0
                for index in range(len(refs)):
                    self._raise_if_cancelled()
                    if requested is not None and len(collected) >= requested:
                        break
                    detail = None
                    try:
                        await self._wait_before_candidate_open()
                        detail = await self._open_candidate_detail_by_index(page, index)
                        detail_text = await self._read_resume_text(detail)
                        if not detail_text:
                            continue
                        detail_url = detail.url
                        key = self._candidate_key(detail_url, detail_text)
                        if key in seen_urls:
                            continue
                        seen_urls.add(key)
                        refs = await self._candidate_refs(page)
                        ref = refs[index] if index < len(refs) else {}
                        summary_text = str(ref.get("summaryText") or "")
                        item = {
                            "order": len(collected) + 1,
                            "pageNumber": page_number,
                            "cardIndex": index,
                            "summaryText": summary_text,
                            "cardProfile": ref.get("cardProfile") or {},
                            "detailText": detail_text,
                            "url": detail_url,
                            "resultsPageUrl": page.url,
                            "success": True,
                        }
                        collected.append(item)
                        page_new += 1
                        await self._emit_progress(
                            stage="crawling",
                            requestedLimit=requested,
                            totalCollected=len(collected),
                            pageNumber=page_number,
                            pageCollected=page_new,
                            currentName=self._guess_candidate_name(detail_text) or self._guess_candidate_name(summary_text),
                        )
                    finally:
                        if detail and not detail.is_closed():
                            await detail.close()
                page_summaries.append(
                    {
                        "pageNumber": page_number,
                        "url": page.url,
                        "attempted": len(refs),
                        "collected": page_new,
                        "totalCollected": len(collected),
                    }
                )
                if requested is not None and len(collected) >= requested:
                    break
                if page_new == 0:
                    break
                moved = await self._go_to_page(page, page_number + 1)
                if not moved:
                    break
                page_number += 1

            self.last_crawl_result = {
                "crawlMode": "saramin_paginated_candidates",
                "requestedLimit": requested,
                "candidateCount": len(collected),
                "pageCount": len(page_summaries),
                "pages": page_summaries,
                "candidates": collected,
            }
            candidates = [self._candidate_from_crawl(item) for item in collected]
            if not candidates:
                raise RuntimeError("No Saramin candidates were collected from the current search page.")
            return candidates
        finally:
            await page.context.browser.close()

    async def open_candidate(self, candidate: Candidate) -> Candidate:
        return candidate

    async def send_proposal(self, candidate: Candidate) -> tuple[bool | None, str]:
        self._raise_if_cancelled()
        position = self.config.offer_position or {}
        if not position.get("id") and not position.get("label"):
            return False, "Saramin offer position is not selected."
        if not candidate.remember_detail_url:
            return False, "Saramin candidate detail URL is missing."

        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright") from exc

        playwright = await async_playwright().start()
        browser = None
        page = None
        try:
            browser = await playwright.chromium.connect_over_cdp(self.config.cdp_url)
            await self._apply_context_overrides(browser.contexts)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            await self._apply_page_overrides(page)
            await page.goto(candidate.remember_detail_url, wait_until="domcontentloaded", timeout=15000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass
            await self._open_offer_modal(page)
            selected = await self._select_offer_position(page, position)
            if not selected.get("ok"):
                return False, str(selected.get("reason") or "Saramin offer position selection failed.")

            action = await self._proposal_modal_action(page, skip_send=self.config.skip_proposal_send)
            if not action.get("ok"):
                return False, str(action.get("reason") or "Saramin proposal send button was not available.")
            if self.config.skip_proposal_send:
                return None, f"Saramin proposal prepared with position: {selected.get('label') or position.get('label') or position.get('id')}"
            return True, f"Saramin proposal sent with position: {selected.get('label') or position.get('label') or position.get('id')}"
        finally:
            if page and not page.is_closed():
                await page.close()
            if browser:
                await browser.close()
            await playwright.stop()

    async def _with_saramin_page(self):
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright") from exc

        playwright = await async_playwright().start()
        browser = await playwright.chromium.connect_over_cdp(self.config.cdp_url)
        browser._headhunting_playwright = playwright
        await self._apply_context_overrides(browser.contexts)
        pages = [page for context in browser.contexts for page in context.pages]
        page = self._select_saramin_search_page(pages)
        if page is None:
            await browser.close()
            await playwright.stop()
            raise RuntimeError("Saramin search tab was not found in the controlled browser.")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        await page.bring_to_front()
        await self._apply_page_overrides(page)

        original_close = browser.close

        async def close_with_playwright(*args, **kwargs):
            try:
                await original_close(*args, **kwargs)
            finally:
                await playwright.stop()

        browser.close = close_with_playwright
        return page

    def _select_saramin_search_page(self, pages):
        for page in pages:
            if "saramin.co.kr/zf_user/memcom/talent-pool/main/search" in page.url:
                return page
        for page in pages:
            if "saramin.co.kr" in page.url:
                return page
        return None

    async def _apply_context_overrides(self, contexts) -> None:
        for context in contexts:
            try:
                await context.set_extra_http_headers({"Accept-Language": self.config.accept_language})
            except Exception:
                pass

    async def _apply_page_overrides(self, page) -> None:
        await page.add_init_script(
            f"""() => {{
                Object.defineProperty(navigator, "webdriver", {{ get: () => undefined }});
                Object.defineProperty(navigator, "language", {{ get: () => "{self.config.locale}" }});
                Object.defineProperty(navigator, "languages", {{ get: () => "{self.config.accept_language}".split(",").map((v) => v.split(";")[0]) }});
            }}""",
        )

    async def _candidate_refs(self, page) -> list[dict[str, Any]]:
        return await page.evaluate(
            """() => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const texts = (root, selector) => Array.from(root.querySelectorAll(selector))
                    .map((node) => clean(node.innerText || node.textContent || ""))
                    .filter((value) => value && !/^\\+\\d+$/.test(value));
                const firstText = (root, selector) => {
                    const node = root.querySelector(selector);
                    return clean(node?.innerText || node?.textContent || "");
                };
                const roots = Array.from(document.querySelectorAll(".talent_list_item"));
                const items = roots.length ? roots : Array.from(document.querySelectorAll(".list_card_item"));
                return items
                    .map((item, index) => {
                        const link = item.querySelector(".summary_info a[href='javascript:void(0)'], a[href='javascript:void(0)']");
                        const text = clean(link?.innerText || link?.textContent || "");
                        const rect = item.getBoundingClientRect();
                        const jobs = texts(item, ".list_jobs_skill:not(.skill_list) .item.jobs, .list_jobs_skill .item.jobs");
                        const skills = texts(item, ".list_jobs_skill.skill_list .item.skills, .skill_list .item.skills");
                        const currentCompany = firstText(item, ".career_list li.now .company_info span")
                            || firstText(item, ".career_list .company_info span");
                        return {
                            index,
                            summaryText: text,
                            itemText: clean(item.innerText || item.textContent || ""),
                            cardProfile: {
                                name: firstText(item, ".personal_info .name, .name"),
                                genderAge: firstText(item, ".personal_info .gender_age, .gender_age"),
                                company: currentCompany,
                                experience: firstText(item, ".personal_info .career_all, .career_all"),
                                location: firstText(item, ".desired_work_area .region"),
                                role: jobs.slice(0, 5).join(", "),
                                jobs,
                                skills,
                            },
                            y: Math.round(rect.top + window.scrollY),
                        };
                    })
                    .filter((item) => item.summaryText.length > 20);
            }"""
        )

    async def _open_first_candidate_detail(self, page):
        refs = await self._candidate_refs(page)
        if not refs:
            raise RuntimeError("No Saramin candidate is visible on the current search page.")
        return await self._open_candidate_detail_by_index(page, 0)

    async def _open_candidate_detail_by_index(self, page, index: int):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        refs = await self._candidate_refs(page)
        if index >= len(refs):
            raise RuntimeError(f"Saramin candidate index {index} was not found.")
        before_pages = set(id(candidate_page) for candidate_page in page.context.pages)
        resume_capture: dict[str, str] = {}

        async def capture_resume_response(response) -> None:
            if "/api/positions/resume/" not in response.url or "/files" not in response.url:
                return
            try:
                payload = json.loads(await response.text())
                html = str(((payload or {}).get("result") or {}).get("resumeHtml") or "")
            except Exception:
                html = ""
            if html:
                resume_capture["html"] = html

        page.context.on("response", capture_resume_response)
        try:
            async with page.context.expect_page(timeout=7000) as page_info:
                await page.evaluate(
                    """(targetIndex) => {
                        const visibleLink = Array.from(document.querySelectorAll(".talent_list_item"))
                            .map((item) => item.querySelector(".summary_info a[href='javascript:void(0)'], a[href='javascript:void(0)']"))
                            .filter(Boolean)[targetIndex];
                        if (!visibleLink) throw new Error("candidate link was not found");
                        visibleLink.scrollIntoView({block: "center", inline: "nearest"});
                        visibleLink.click();
                    }""",
                    index,
                )
            detail = await page_info.value
        except PlaywrightTimeoutError:
            await page.wait_for_timeout(1500)
            detail = next((candidate_page for candidate_page in page.context.pages if id(candidate_page) not in before_pages), None)
            if detail is None:
                raise RuntimeError("Saramin candidate detail tab did not open.")
        await detail.wait_for_load_state("domcontentloaded", timeout=10000)
        for _ in range(28):
            if resume_capture.get("html"):
                break
            await detail.wait_for_timeout(250)
        detail._saramin_resume_html = resume_capture.get("html", "")
        await self._apply_page_overrides(detail)
        try:
            page.context.remove_listener("response", capture_resume_response)
        except Exception:
            pass
        return detail

    async def _read_resume_text(self, page) -> str:
        api_text = await self._read_resume_html_text(page)
        dom_text = await page.evaluate(
            """() => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const main = document.querySelector("main") || document.body;
                return clean(main?.innerText || main?.textContent || "");
            }"""
        )
        text = api_text if len(api_text) >= max(300, len(dom_text) * 2) else dom_text
        return self._clean_resume_text(text)

    async def _read_resume_html_text(self, page) -> str:
        captured_html = str(getattr(page, "_saramin_resume_html", "") or "")
        if captured_html:
            return await self._resume_html_to_text(page, captured_html)
        token = await self._applicant_token(page)
        if not token:
            return ""
        try:
            text = await page.evaluate(
                """async (token) => {
                    const match = location.pathname.match(/\\/resume\\/(\\d+)/);
                    if (!match) return "";
                    const response = await fetch(`https://api-hiring.saramin.co.kr/api/positions/resume/${match[1]}/files?`, {
                        credentials: "include",
                        headers: {
                            "Accept": "*/*",
                            "x-applicant-token": token,
                        },
                    });
                    if (!response.ok) return "";
                    const data = await response.json();
                    const html = data?.result?.resumeHtml || "";
                    if (!html) return "";

                    const parser = new DOMParser();
                    const doc = parser.parseFromString(html, "text/html");
                    doc.querySelectorAll("script, style, noscript, svg, iframe, canvas").forEach((node) => node.remove());

                    const blockTags = new Set([
                        "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "fieldset",
                        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
                        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody",
                        "td", "tfoot", "th", "thead", "tr", "ul",
                    ]);
                    const hidden = (el) => {
                        const ariaHidden = el.getAttribute("aria-hidden") === "true";
                        const hiddenAttr = el.hasAttribute("hidden");
                        const style = String(el.getAttribute("style") || "").replace(/\\s+/g, "").toLowerCase();
                        return ariaHidden || hiddenAttr || style.includes("display:none") || style.includes("visibility:hidden");
                    };
                    const walk = (node) => {
                        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
                        if (node.nodeType !== Node.ELEMENT_NODE) return "";
                        const tag = node.tagName.toLowerCase();
                        if (tag === "br") return "\\n";
                        if (hidden(node)) return "";
                        let value = "";
                        for (const child of node.childNodes) value += walk(child);
                        if (tag === "td" || tag === "th") return ` ${value.trim()} `;
                        if (blockTags.has(tag)) return `\\n${value.trim()}\\n`;
                        return value;
                    };
                    return walk(doc.body || doc.documentElement)
                        .replace(/\\r/g, "\\n")
                        .replace(/[ \\t\\f\\v]+/g, " ")
                        .replace(/ *\\n+ */g, "\\n")
                        .replace(/\\n{3,}/g, "\\n\\n")
                        .trim();
                }""",
                token,
            )
        except Exception:
            return ""
        return str(text or "").strip()

    async def _resume_html_to_text(self, page, html: str) -> str:
        if not html:
            return ""
        try:
            text = await page.evaluate(
                """(html) => {
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(html, "text/html");
                    doc.querySelectorAll("script, style, noscript, svg, iframe, canvas").forEach((node) => node.remove());

                    const blockTags = new Set([
                        "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "fieldset",
                        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
                        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody",
                        "td", "tfoot", "th", "thead", "tr", "ul",
                    ]);
                    const hidden = (el) => {
                        const ariaHidden = el.getAttribute("aria-hidden") === "true";
                        const hiddenAttr = el.hasAttribute("hidden");
                        const style = String(el.getAttribute("style") || "").replace(/\\s+/g, "").toLowerCase();
                        return ariaHidden || hiddenAttr || style.includes("display:none") || style.includes("visibility:hidden");
                    };
                    const walk = (node) => {
                        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
                        if (node.nodeType !== Node.ELEMENT_NODE) return "";
                        const tag = node.tagName.toLowerCase();
                        if (tag === "br") return "\\n";
                        if (hidden(node)) return "";
                        let value = "";
                        for (const child of node.childNodes) value += walk(child);
                        if (tag === "td" || tag === "th") return ` ${value.trim()} `;
                        if (blockTags.has(tag)) return `\\n${value.trim()}\\n`;
                        return value;
                    };
                    return walk(doc.body || doc.documentElement)
                        .replace(/\\r/g, "\\n")
                        .replace(/[ \\t\\f\\v]+/g, " ")
                        .replace(/ *\\n+ */g, "\\n")
                        .replace(/\\n{3,}/g, "\\n\\n")
                        .trim();
                }""",
                html,
            )
        except Exception:
            return ""
        return str(text or "").strip()

    async def _applicant_token(self, page) -> str:
        patterns = [
            r'"token"\s*:\s*"([0-9a-fA-F-]{36})"',
            r'\\"token\\"\s*:\s*\\"([0-9a-fA-F-]{36})\\"',
            r'\\\\"token\\\\"\s*:\s*\\\\"([0-9a-fA-F-]{36})\\\\"',
        ]
        for _ in range(24):
            try:
                content = await page.content()
            except Exception:
                content = ""
            for pattern in patterns:
                match = re.search(pattern, content)
                if match:
                    return match.group(1)
            await page.wait_for_timeout(250)
        return ""

    def _clean_resume_text(self, text: str) -> str:
        text = " ".join(str(text or "").split())
        remove_phrases = [
            "후보자가 제안을 수락한 후, 상세주소와 연락처를 확인하실 수 있습니다.",
        ]
        for phrase in remove_phrases:
            text = text.replace(phrase, " ")

        text = re.sub(r"\S*님이 설정한 이력서 조건.*$", "", text).strip()

        stop_markers = [
            "위의 모든 내용은 사실과 다름 없음을 확인합니다.",
            "작성자 :",
            "상기 이력서는",
            "위조된 문서를 등록하여 취업활동에 이용시 법적 책임을 지게 될 수 있습니다.",
            "본 정보는 취업활동을 위해 등록된 개인 이력서 정보이며",
            "본 정보는 채용의 목적으로만 사용해야하며",
            "개인정보와 개인정보가 담긴 출력 및 복사물을 불법 유출하는 경우에는",
            "방금 본 이력서와 많이 닮은 후보자",
            "자타공인, 좋은 경력을 보유한 후보자",
            "피드 팀 게시판 내 메모",
            "면접 0 아직 면접 내역",
        ]
        for marker in stop_markers:
            index = text.find(marker)
            if index >= 0:
                text = text[:index].strip()
        return " ".join(text.split())

    async def _open_offer_modal(self, page) -> None:
        result = await page.evaluate(
            """() => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
                };
                if (document.querySelector("#job-offer-position-selector")) {
                    return {ok: true, alreadyOpen: true};
                }
                const buttons = Array.from(document.querySelectorAll("button")).filter(visible);
                const button = buttons.find((node) => {
                    const text = clean(node.innerText || node.textContent || "");
                    return text.includes("이직") && text.includes("제안");
                }) || buttons[0];
                if (!button) return {ok: false, reason: "offer button was not found"};
                button.scrollIntoView({block: "center", inline: "nearest"});
                button.click();
                return {ok: true, text: clean(button.innerText || button.textContent || "")};
            }"""
        )
        if not result.get("ok"):
            raise RuntimeError(str(result.get("reason") or "Saramin offer button was not found."))
        for _ in range(20):
            exists = await page.evaluate("""() => Boolean(document.querySelector("#job-offer-position-selector"))""")
            if exists:
                return
            await page.wait_for_timeout(250)
        raise RuntimeError("Saramin offer modal did not open.")

    async def _read_offer_position_options(self, page) -> list[dict[str, str]]:
        await page.evaluate(
            """() => {
                const combo = document.querySelector("#job-offer-position-selector [role='combobox']");
                if (combo) combo.click();
            }"""
        )
        await page.wait_for_timeout(700)
        options = await page.evaluate(
            """() => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                return Array.from(document.querySelectorAll("#job-offer-position-selector [role='option'], #offer-position-option-list [role='option']"))
                    .map((node) => ({
                        id: String(node.id || "").trim(),
                        label: clean(node.innerText || node.textContent || ""),
                    }))
                    .filter((item) => /^\\d+$/.test(item.id) && item.label);
            }"""
        )
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for option in options:
            key = option.get("id") or option.get("label") or ""
            if key and key not in seen:
                seen.add(key)
                unique.append({"id": str(option.get("id") or ""), "label": str(option.get("label") or "")})
        return unique

    async def _select_offer_position(self, page, position: dict[str, str]) -> dict[str, Any]:
        target_id = str(position.get("id") or "").strip()
        target_label = str(position.get("label") or "").strip()
        opened = await page.evaluate(
            """() => {
                const selector = document.querySelector("#job-offer-position-selector");
                const combo = selector?.querySelector("[role='combobox']");
                if (!selector || !combo) return false;
                combo.click();
                return true;
            }"""
        )
        if not opened:
            return {"ok": False, "reason": "position combobox was not found"}
        await page.wait_for_timeout(700)
        result = await page.evaluate(
            """(target) => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const selector = document.querySelector("#job-offer-position-selector");
                if (!selector) return {ok: false, reason: "position combobox was not found"};
                const options = Array.from(document.querySelectorAll("#job-offer-position-selector [role='option'], #offer-position-option-list [role='option']"));
                const option = options.find((node) => target.id && String(node.id || "") === target.id)
                    || options.find((node) => target.label && clean(node.innerText || node.textContent || "") === target.label)
                    || options.find((node) => target.label && clean(node.innerText || node.textContent || "").includes(target.label));
                if (!option) {
                    return {
                        ok: false,
                        reason: "selected position was not found",
                        available: options.map((node) => ({id: String(node.id || ""), label: clean(node.innerText || node.textContent || "")})),
                    };
                }
                option.click();
                return {ok: true, id: String(option.id || ""), label: clean(option.innerText || option.textContent || "")};
            }""",
            {"id": target_id, "label": target_label},
        )
        await page.wait_for_timeout(900)
        return result

    async def _proposal_modal_action(self, page, skip_send: bool) -> dict[str, Any]:
        result = await page.evaluate(
            """(skipSend) => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
                };
                const buttons = Array.from(document.querySelectorAll("button")).filter(visible);
                const sendButton = buttons.reverse().find((node) => clean(node.innerText || node.textContent || "").includes("제안 발송"));
                if (!sendButton) {
                    return {
                        ok: false,
                        reason: "send button was not found",
                        buttons: buttons.slice(0, 30).map((node) => clean(node.innerText || node.textContent || "")),
                    };
                }
                if (sendButton.disabled) {
                    return {ok: false, reason: "send button is disabled"};
                }
                if (skipSend) {
                    return {ok: true, skipped: true};
                }
                sendButton.click();
                return {ok: true, clicked: true};
            }""",
            skip_send,
        )
        if result.get("clicked"):
            await page.wait_for_timeout(1200)
        return result

    async def _go_to_page(self, page, page_number: int) -> bool:
        before = await self._first_candidate_signature(page)
        clicked = await page.evaluate(
            """(pageNumber) => {
                const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const buttons = Array.from(document.querySelectorAll("button"));
                const target = buttons.find((node) => clean(node.innerText || node.textContent || "") === String(pageNumber));
                if (!target || target.disabled) return false;
                target.scrollIntoView({block: "center", inline: "nearest"});
                target.click();
                return true;
            }""",
            page_number,
        )
        if not clicked:
            return False
        for _ in range(30):
            await page.wait_for_timeout(300)
            after = await self._first_candidate_signature(page)
            if after and after != before:
                await page.evaluate("window.scrollTo(0, 0)")
                return True
        return False

    async def _first_candidate_signature(self, page) -> str:
        refs = await self._candidate_refs(page)
        return self._text_hash(refs[0]["summaryText"]) if refs else ""

    async def _emit_progress(self, **payload: Any) -> None:
        self._raise_if_cancelled()
        if self.progress_callback:
            await self.progress_callback(payload)
        self._raise_if_cancelled()

    async def _wait_before_candidate_open(self) -> None:
        self._raise_if_cancelled()
        await asyncio.sleep(random.uniform(1.0, 2.0))
        self._raise_if_cancelled()

    def _candidate_from_crawl(self, item: dict[str, Any]) -> Candidate:
        order = int(item.get("order") or 0)
        text = str(item.get("detailText") or "").strip()
        summary = str(item.get("summaryText") or "")
        detail_profile = self._profile_metadata(text or summary)
        card_profile = self._normalize_card_profile(item.get("cardProfile") if isinstance(item.get("cardProfile"), dict) else {})
        name = card_profile.get("name") or detail_profile.get("name") or self._guess_candidate_name(summary) or f"Saramin-{order}"
        skills = self._unique_values([*card_profile.get("jobs", []), *card_profile.get("skills", [])])
        return Candidate(
            id=f"saramin-{order:02d}-{self._text_hash(str(item.get('url') or text))}",
            name=name,
            company=card_profile.get("company") or detail_profile.get("company") or "-",
            role=card_profile.get("role") or detail_profile.get("role") or "-",
            experience=card_profile.get("experience") or detail_profile.get("experience") or "-",
            location=card_profile.get("location") or detail_profile.get("location") or "-",
            skills=skills or detail_profile.get("skills", []),
            resume_text=text,
            remember_page_number=int(item.get("pageNumber") or 0) or None,
            remember_page_url=str(item.get("resultsPageUrl") or "") or None,
            remember_detail_url=str(item.get("url") or "") or None,
            remember_profile_id=self._profile_id_from_url(str(item.get("url") or "")) or None,
            remember_profile_card_id=None,
            crawl_order=order or None,
            remember_card_index=int(item.get("cardIndex") or 0) if item.get("cardIndex") is not None else None,
            remember_card_text=summary[:1200] or None,
        )

    def _normalize_card_profile(self, value: dict[str, Any]) -> dict[str, Any]:
        jobs = self._clean_card_terms(value.get("jobs"))
        skills = self._clean_card_terms(value.get("skills"))
        role = self._clean_profile_value(value.get("role"))
        if not role and jobs:
            role = ", ".join(jobs[:3])
        return {
            "name": self._clean_profile_value(value.get("name")),
            "company": self._clean_profile_value(value.get("company")),
            "role": role,
            "experience": self._clean_profile_value(value.get("experience")),
            "location": self._clean_profile_value(value.get("location")),
            "jobs": jobs,
            "skills": skills,
        }

    @staticmethod
    def _clean_profile_value(value: Any) -> str:
        text = " ".join(str(value or "").split())
        return "" if not text or text == "-" or re.fullmatch(r"\+\d+", text) else text

    @classmethod
    def _clean_card_terms(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return cls._unique_values(cls._clean_profile_value(item) for item in value)

    @staticmethod
    def _unique_values(values) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = " ".join(str(value or "").split())
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _profile_metadata(self, text: str) -> dict[str, Any]:
        compact = " ".join(str(text or "").split())
        name = self._guess_candidate_name(compact)
        experience = self._saramin_experience(compact)
        location = self._saramin_location(compact)
        skills = self._saramin_skills(compact)
        role = ", ".join(skills[:3]) if skills else "-"
        return {
            "name": name,
            "company": "-",
            "role": role,
            "experience": experience,
            "location": location,
            "skills": skills,
        }

    @staticmethod
    def _saramin_experience(text: str) -> str:
        head = text[:500]
        if re.search(r"\b신입\b", head):
            return "신입"
        match = re.search(r"경력\s*(?:총\s*)?(\d+\s*년(?:\s*\d+\s*개월)?)", head)
        if match:
            return f"경력 {match.group(1).replace(' ', '')}"
        match = re.search(r"경력\s*(\d+\s*개월)", head)
        if match:
            return f"경력 {match.group(1).replace(' ', '')}"
        return "-"

    @staticmethod
    def _saramin_location(text: str) -> str:
        provinces = "서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주"
        match = re.search(
            rf"(?:남|여)?(?:,\s*)?(?:19|20)\d{{2}}\s*\(\d{{2}}세\)\s*((?:{provinces})(?:\s+[가-힣]+구|\s+[가-힣]+시|\s+[가-힣]+군)?)",
            text[:500],
        )
        if match:
            return " ".join(match.group(1).split())
        match = re.search(rf"\b({provinces})(?:\s+[가-힣]+구|\s+[가-힣]+시|\s+[가-힣]+군)?\b", text[:500])
        return " ".join(match.group(0).split()) if match else "-"

    @staticmethod
    def _saramin_skills(text: str) -> list[str]:
        role_keywords = [
            "기술영업",
            "영업관리",
            "영업지원",
            "해외영업",
            "해외영업지원",
            "해외영업관리",
            "기업영업",
            "거래처영업",
            "영업기획",
            "IT영업",
            "영업전략",
            "영업마케팅",
            "마케팅",
            "기획",
            "전략기획",
            "구매관리",
            "개발",
            "연구원",
            "R&D",
            "PM",
            "PO",
        ]
        skill_source = text
        match = re.search(r"나의 스킬\s+(.+?)(?:\s+학력\s+|\s+경력\s+|\s+경험/활동/교육\s+|\s+자격/어학/수상\s+)", text)
        if match:
            skill_source = match.group(1)
        skills: list[str] = []
        for keyword in role_keywords:
            if keyword in skill_source and keyword not in skills:
                skills.append(keyword)
            if len(skills) >= 12:
                break
        if not skills and skill_source != text:
            for keyword in role_keywords:
                if keyword in text and keyword not in skills:
                    skills.append(keyword)
                if len(skills) >= 12:
                    break
        return skills

    @staticmethod
    def _guess_candidate_name(text: str) -> str:
        value = str(text or "")
        match = re.search(r"이력서\s+VIEW\s+([가-힣○O]{2,5})", value)
        if match:
            return match.group(1)
        match = re.search(r"\b([가-힣○O]{2,5})\s+(?:신입|경력|남|여|(?:19|20)\d{2})\b", value)
        if match:
            return match.group(1)
        match = re.search(r"[가-힣][○O]{2}", value)
        return match.group(0) if match else ""

    @staticmethod
    def _profile_id_from_url(url: str) -> str:
        match = re.search(r"/resume/(\d+)", url)
        return match.group(1) if match else ""

    @staticmethod
    def _candidate_key(url: str, text: str) -> str:
        profile_id = BrowserSaraminAdapter._profile_id_from_url(url)
        if profile_id:
            return f"profile:{profile_id}"
        return BrowserSaraminAdapter._text_hash(text)

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]
