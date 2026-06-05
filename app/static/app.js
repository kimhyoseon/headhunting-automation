const sampleJD = `[채용 포지션]
시니어 프로덕트 디자이너 (Lead 후보)

[회사]
토스플레이스 - 핀테크 결제 SaaS, 시리즈 C, 임직원 280명
오프라인 가맹점 결제 단말기와 정산 대시보드를 만드는 B2B 핀테크 팀입니다.

[자격 요건]
- 프로덕트 디자인 5년 이상, 그중 B2B/SaaS 경험 2년 이상
- Figma 능숙, 디자인시스템 구축/운영 경험
- 사용성 리서치 직접 수행 경험
- 데이터 기반 의사결정 경험

[우대 사항]
- 핀테크/결제/정산 도메인 경험
- 디자인 리드/멘토링 경험
- 영어 비동기 협업 가능

[근무 조건]
서울 강남 · 풀타임 · 주 1회 재택 · 경력 5~9년
연봉 8,500~11,000만원 + 스톡옵션`;

const defaultSendSort = { key: "crawlOrder", direction: "asc" };

const state = {
  run: null,
  socket: null,
  settings: null,
  sendSort: { ...defaultSendSort },
  sendSelection: {},
  saraminOfferPosition: null,
  saraminOfferPositions: [],
  saraminOfferStatus: "",
  autoSendRunId: null,
  currentStep: 1,
};

const $ = (id) => document.getElementById(id);
const draftKey = "headhunting-mvp-draft";
const flagshipModels = ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro"];
const defaultOpenAIModel = "gpt-5.4-mini";
const defaultMaxCandidates = 30;

function init() {
  loadDraft();
  updateJDCount();
  bindEvents();
  loadSettings();
}

function bindEvents() {
  $("jd-text").addEventListener("input", () => {
    updateJDCount();
    saveDraft();
  });
  $("threshold").addEventListener("input", (event) => {
    $("threshold-label").textContent = event.target.value;
    saveDraft();
  });
  bindLimitControl("max-candidates", 1);
  $("reset-input").addEventListener("click", () => {
    $("jd-text").value = sampleJD;
    $("threshold").value = 90;
    $("threshold-label").textContent = "90";
    setLimitControl("max-candidates", defaultMaxCandidates);
    updateJDCount();
    saveDraft();
  });
  $("generate-btn").addEventListener("click", startRunFromButton);
  $("crawl-test-btn")?.addEventListener("click", runCrawlTest);
  $("saramin-position-btn")?.addEventListener("click", chooseSaraminOfferPosition);
  $("saramin-position-options")?.addEventListener("click", handleSaraminOfferPositionClick);
  $("cancel-btn").addEventListener("click", cancelRun);
  $("send-selected-btn").addEventListener("click", sendSelected);
  $("send-threshold").addEventListener("input", updateSendThreshold);
  $("send-threshold").addEventListener("change", updateSendThreshold);
  $("send-select-all").addEventListener("change", handleSendSelectAllChange);
  $("send-table").addEventListener("click", handleSendTableClick);
  $("send-table").addEventListener("change", handleSendTableChange);
  $("download-summary-btn").addEventListener("click", downloadRunSummary);
  $("new-run-btn").addEventListener("click", () => {
    showStep(1);
    state.run = null;
    state.autoSendRunId = null;
    state.sendSelection = {};
    $("run-meta").textContent = "실행 전";
  });
  document.querySelectorAll("[data-go]").forEach((button) => {
    button.addEventListener("click", () => showStep(Number(button.dataset.go)));
  });
  $("settings-open-btn").addEventListener("click", openSettings);
  $("settings-close-btn").addEventListener("click", closeSettings);
  $("settings-cancel-btn").addEventListener("click", closeSettings);
  $("settings-save-btn").addEventListener("click", saveSettings);
  $("settings-model-preset").addEventListener("change", syncModelFromPreset);
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.addEventListener("click", () => showSettingsTab(button.dataset.settingsTab));
  });
  $("settings-modal").addEventListener("click", (event) => {
    if (event.target === $("settings-modal")) closeSettings();
  });
}

function loadDraft() {
  const draft = readDraft();
  $("jd-text").value = draft.jd_text || sampleJD;
  $("threshold").value = draft.threshold ?? 90;
  $("threshold-label").textContent = $("threshold").value;
  setLimitControl("max-candidates", Object.prototype.hasOwnProperty.call(draft, "max_candidate_count") ? draft.max_candidate_count : defaultMaxCandidates);
}

function readDraft() {
  try {
    return JSON.parse(localStorage.getItem(draftKey) || "{}");
  } catch {
    return {};
  }
}

function saveDraft() {
  const payload = {
    jd_text: $("jd-text").value,
    threshold: Number($("threshold").value),
    max_candidate_count: readLimitValue("max-candidates", 1),
  };
  try {
    localStorage.setItem(draftKey, JSON.stringify(payload));
  } catch {
    // Browser private mode or a locked-down client PC can block localStorage.
  }
}

function bindLimitControl(prefix, minValue) {
  const syncFromRange = (event) => {
    setLimitControl(prefix, Number(event.target.value));
    saveDraft();
  };
  $(`${prefix}-range`).addEventListener("input", syncFromRange);
  $(`${prefix}-range`).addEventListener("change", syncFromRange);
  $(`${prefix}-input`).addEventListener("change", () => {
    setLimitControl(prefix, readLimitValue(prefix, minValue));
    saveDraft();
  });
  $(`${prefix}-input`).addEventListener("blur", () => {
    setLimitControl(prefix, readLimitValue(prefix, minValue));
    saveDraft();
  });
}

function setLimitControl(prefix, value) {
  const range = $(`${prefix}-range`);
  const input = $(`${prefix}-input`);
  const label = $(`${prefix}-label`);

  if (value === null) {
    range.value = range.max;
    input.value = "무한대";
    label.textContent = "무한대";
    return;
  }

  const min = Number(range.min);
  const max = Number(range.max);
  const normalized = Math.max(min, Math.floor(Number(value || min)));
  range.value = Math.min(max, normalized);
  input.value = String(normalized);
  label.textContent = `${normalized.toLocaleString("ko-KR")}명`;
}

function readLimitValue(prefix, minValue) {
  const text = $(`${prefix}-input`).value.trim().toLowerCase();
  if (["", "무한대", "무제한", "제한없음", "infinite", "infinity", "unlimited", "∞"].includes(text)) {
    return null;
  }
  const parsed = Number(text.replaceAll(",", ""));
  if (Number.isFinite(parsed)) {
    return Math.max(minValue, Math.floor(parsed));
  }
  return Math.max(minValue, Number($(`${prefix}-range`).value || minValue));
}

function updateJDCount() {
  $("jd-count").textContent = `${$("jd-text").value.length.toLocaleString()}자`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

async function startRunFromButton() {
  const button = $("generate-btn");
  button.disabled = true;
  button.textContent = isSaraminProvider() ? "사람인 검색 확인 중..." : "리멤버 검색수 확인 중...";
  try {
    if (isSaraminProvider() && !state.saraminOfferPosition) {
      button.textContent = "사람인 제안 포지션 선택 중...";
      const selected = await chooseSaraminOfferPosition();
      if (!selected) return;
    }
    saveDraft();
    await startRun();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "분석 시작";
  }
}

async function startRun() {
  const payload = {
    jd_text: $("jd-text").value,
    threshold: Number($("threshold").value),
    test_mode: state.settings?.confirm_before_proposal_send === true,
    max_candidate_count: readLimitValue("max-candidates", 1),
  };
  saveDraft();
  const run = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
  state.run = run;
  state.autoSendRunId = null;
  state.sendSort = { ...defaultSendSort };
  state.sendSelection = {};
  $("send-threshold").value = payload.threshold;
  $("send-threshold-label").textContent = String(payload.threshold);
  $("mode-pill").textContent = run.config?.test_mode ? "확인 후 발송 ON" : "확인 후 발송 OFF";
  $("run-meta").textContent = `실행 ID ${run.run_id}`;
  showStep(2);
  connectSocket(run.run_id);
  renderRun(run);
}

function isSaraminProvider() {
  return state.settings?.provider === "saramin";
}

function providerDisplayName() {
  return isSaraminProvider() ? "사람인" : "리멤버";
}

function providerRunFileName() {
  return isSaraminProvider() ? "run_saramin.bat" : "run_app.bat";
}

function providerDefaultUrl() {
  return isSaraminProvider()
    ? "https://www.saramin.co.kr/zf_user/memcom/talent-pool/main/search"
    : "https://career.rememberapp.co.kr/";
}

function renderProviderCopy() {
  const provider = providerDisplayName();
  const runFile = providerRunFileName();
  $("provider-session-copy").textContent = `브라우저 ${provider} 세션 연결`;
  $("provider-page-copy").textContent = `JD와 현재 ${provider} 창의 후보자 정보를 비교해 매칭 분석을 진행합니다.`;
  $("crawl-test-title").textContent = `${provider} 탭 HTML 테스트`;
  $("settings-crawler-mode-copy").textContent = `현재는 목 데이터가 기본값입니다. 실제 ${provider} 접근 PC에서 브라우저 연결 모드로 전환합니다.`;
  $("settings-provider-url-label").textContent = `${provider} 시작 URL`;
  $("settings-provider-url-copy").textContent = `전용 브라우저를 열 때 앱 탭과 함께 열릴 ${provider} 탭 주소입니다.`;
  $("settings-provider-language-copy").textContent = `${provider} 탭의 이후 요청에 적용됩니다. 기존 로드 요청에는 적용되지 않습니다.`;
  $("settings-provider-run-copy").innerHTML = `<code>${runFile}</code>을 실행하면 서버가 켜지고, 같은 Chrome/Edge 창 안에 앱 탭과 ${provider} 탭이 함께 열립니다. 사용자는 ${provider} 탭에서 로그인과 검색 조건 설정을 마친 뒤 앱 탭에서 시작 버튼을 누르는 흐름으로 진행합니다.`;
  $("settings-remember-url").placeholder = providerDefaultUrl();
}

function renderSaraminProviderControls() {
  const card = $("saramin-position-card");
  if (!card) return;
  card.hidden = !isSaraminProvider();
  if (!isSaraminProvider()) return;
  const position = state.saraminOfferPosition;
  const badge = $("saramin-position-badge");
  const label = $("saramin-position-label");
  const options = $("saramin-position-options");
  badge.textContent = position ? "선택됨" : "미선택";
  badge.classList.toggle("selected", Boolean(position));
  $("saramin-position-status").textContent = state.saraminOfferStatus || (position ? "선택 완료" : "제안 발송에 사용할 포지션을 먼저 선택하세요.");
  label.textContent = position ? formatSaraminPosition(position) : "선택된 포지션 없음";
  label.classList.toggle("empty", !position);
  renderSaraminOfferPositionOptions(options);
}

async function chooseSaraminOfferPosition() {
  const button = $("saramin-position-btn");
  if (button) {
    button.disabled = true;
    button.textContent = "포지션 불러오는 중...";
  }
  try {
    state.saraminOfferStatus = "사람인 후보자 상세에서 제안 포지션을 확인 중입니다.";
    renderSaraminProviderControls();
    const data = await api("/api/saramin/offer-positions");
    const positions = data.positions || [];
    state.saraminOfferPositions = positions;
    if (!positions.length) {
      state.saraminOfferStatus = data.reason || "사람인 제안 포지션을 찾지 못했습니다.";
      renderSaraminProviderControls();
      return false;
    }
    if (positions.length === 1) {
      return await saveSaraminOfferPosition(positions[0]);
    }
    state.saraminOfferStatus = `${positions.length}개 포지션을 찾았습니다. 사용할 포지션을 하나 선택하세요.`;
    renderSaraminProviderControls();
    return false;
  } catch (error) {
    state.saraminOfferStatus = error.message;
    renderSaraminProviderControls();
    return false;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "제안 포지션 선택";
    }
  }
}

function renderSaraminOfferPositionOptions(container) {
  if (!container) return;
  if (!state.saraminOfferPositions.length) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  const selectedId = state.saraminOfferPosition?.id || "";
  container.hidden = false;
  container.innerHTML = state.saraminOfferPositions.map((position) => {
    const selected = selectedId && selectedId === position.id;
    return `
      <button class="saramin-option-button${selected ? " selected" : ""}" type="button" data-position-id="${escapeAttr(position.id || "")}">
        ${escapeHtml(formatSaraminPosition(position))}
      </button>
    `;
  }).join("");
}

async function handleSaraminOfferPositionClick(event) {
  const button = event.target.closest(".saramin-option-button");
  if (!button) return;
  const position = state.saraminOfferPositions.find((item) => item.id === button.dataset.positionId);
  if (!position) return;
  button.disabled = true;
  const saved = await saveSaraminOfferPosition(position);
  button.disabled = false;
  return saved;
}

async function saveSaraminOfferPosition(position) {
  const saved = await api("/api/saramin/offer-position", {
    method: "POST",
    body: JSON.stringify({ id: position.id || "", label: position.label || "" }),
  });
  state.saraminOfferPosition = saved.position || position;
  state.saraminOfferStatus = "선택 완료";
  renderSaraminProviderControls();
  return true;
}

function formatSaraminPosition(position) {
  const label = position?.label || position?.id || "-";
  return position?.id ? `${label} (${position.id})` : label;
}

async function runCrawlTest() {
  const provider = providerDisplayName();
  const button = $("crawl-test-btn");
  const panel = $("crawl-test-panel");
  const status = $("crawl-test-status");
  const badge = $("crawl-test-badge");
  const output = $("crawl-test-output");

  panel.hidden = false;
  button.disabled = true;
  button.textContent = "확인 중...";
  status.textContent = `CDP 브라우저에 연결해서 ${provider} 탭 DOM을 확인하는 중입니다.`;
  badge.textContent = "실행 중";
  output.textContent = `크롤링 요청 전송 중...\n${provider} 탭에서 현재 화면의 인재 카드 2명을 순서대로 확인합니다.`;

  try {
    const data = await api("/api/remember/html-test", { method: "POST", body: "{}" });
    badge.textContent = data.found ? "성공" : "탭 없음";
    const candidateNames = (data.candidates || []).map((candidate) => candidate.name).filter(Boolean).join(", ");
    status.textContent = data.found
      ? `수집 완료 · ${Number(data.candidateCount || 0).toLocaleString("ko-KR")}명${candidateNames ? ` (${candidateNames})` : ""}`
      : data.message || `브라우저에는 연결했지만 ${provider} 탭을 찾지 못했습니다.`;
    output.textContent = formatCrawlData(data);
  } catch (error) {
    badge.textContent = "실패";
    status.textContent = error.message;
    output.textContent = error.stack || error.message;
  } finally {
    button.disabled = false;
    button.textContent = "크롤링 테스트";
  }
}

function formatCrawlData(data) {
  if (!data || !data.found) {
    return JSON.stringify(data, null, 2);
  }
  const lines = [
    `Candidates: ${Number(data.candidateCount || 0).toLocaleString("ko-KR")} / ${Number(data.requestedLimit || 0).toLocaleString("ko-KR")}`,
    "",
    "[Candidates]",
    ...formatCrawlCandidates(data.candidates || []),
    "",
    "[Page]",
    `URL: ${data.url || "-"}`,
    `Title: ${data.title || "-"}`,
    `HTML: ${Number(data.htmlLength || 0).toLocaleString("ko-KR")} chars`,
    `Text: ${Number(data.textLength || 0).toLocaleString("ko-KR")} chars`,
    `ScrollY: ${Number(data.scrollY || 0).toLocaleString("ko-KR")}`,
    "",
    "[Actions]",
    ...(data.demo_actions || []).map((item, index) => `${index + 1}. ${item}`),
    "",
    "[Extracted text samples]",
    ...((data.extractedItems || []).length ? data.extractedItems : ["No visible text samples found."]).map((item, index) => `${index + 1}. ${item}`),
    "",
    "[Button samples]",
    ...((data.buttonSamples || []).length ? data.buttonSamples : ["No button text found."]).map((item, index) => `${index + 1}. ${item}`),
    "",
    "[Environment]",
    JSON.stringify(data.environment || {}, null, 2),
  ];
  return lines.join("\n");
}

function formatCrawlCandidates(candidates) {
  if (!candidates.length) {
    return ["No candidate cards collected."];
  }
  return candidates.flatMap((candidate) => [
    `${candidate.order}. ${candidate.name || "-"} · ${candidate.success ? "OK" : "FAILED"} · ${candidate.detailSource || "-"} · ${Number(candidate.detailLength || 0).toLocaleString("ko-KR")} chars`,
    `   ${String(candidate.detailText || candidate.reason || "").slice(0, 1000)}`,
  ]);
}

async function loadSettings() {
  try {
    const settings = await api("/api/settings");
    state.settings = settings;
    renderSettings(settings);
  } catch {
    $("llm-source").textContent = "Settings load failed";
  }
}

function renderSettings(settings) {
  renderProviderCopy();
  $("settings-api-key").value = "";
  $("settings-key-status").textContent = settings.api_key_set
    ? `저장된 키 있음 (${settings.api_key_preview})`
    : "저장된 키 없음";
  setModelControls(settings.openai_model || defaultOpenAIModel);
  $("settings-host").value = settings.app_host;
  $("settings-port").value = settings.app_port;
  $("settings-min-delay").value = settings.min_delay_seconds;
  $("settings-max-delay").value = settings.max_delay_seconds;
  $("settings-usd-krw-rate").value = settings.usd_krw_rate;
  $("settings-crawler-mode").value = settings.crawler_mode || "mock";
  $("settings-remember-url").value = settings.remember_url || providerDefaultUrl();
  $("settings-remember-cdp-url").value = settings.remember_cdp_url || "http://127.0.0.1:9222";
  $("settings-remember-browser-port").value = settings.remember_browser_port || 9222;
  $("settings-remember-browser-profile-dir").value = settings.remember_browser_profile_dir || "browser_profile";
  $("settings-browser-locale").value = settings.browser_locale || "ko-KR";
  $("settings-browser-accept-language").value = settings.browser_accept_language || "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7";
  $("settings-browser-timezone").value = settings.browser_timezone || "Asia/Seoul";
  $("settings-confirm-before-proposal-send").checked = settings.confirm_before_proposal_send === true;
  $("settings-remember-skip-proposal-send").checked = settings.remember_skip_proposal_send !== false;
  const prompts = settings.prompts || {};
  $("settings-match-system-prompt").value = prompts.match_system_prompt || "";
  $("settings-match-user-prompt").value = prompts.match_user_prompt || "";
  $("settings-clear-key").checked = false;
  $("llm-source").textContent = settings.api_key_set ? "OpenAI key saved" : "OpenAI fallback ready";
  renderSaraminProviderControls();
}

async function openSettings() {
  await loadSettings();
  $("settings-save-status").textContent = "";
  showSettingsTab("runtime");
  $("settings-modal").classList.add("open");
  $("settings-modal").setAttribute("aria-hidden", "false");
}

function closeSettings() {
  $("settings-modal").classList.remove("open");
  $("settings-modal").setAttribute("aria-hidden", "true");
}

async function saveSettings() {
  const button = $("settings-save-btn");
  const status = $("settings-save-status");
  button.disabled = true;
  status.textContent = "저장 중...";
  try {
    const payload = {
      openai_api_key: $("settings-api-key").value.trim() || null,
      clear_api_key: $("settings-clear-key").checked,
      openai_model: $("settings-model").value.trim() || defaultOpenAIModel,
      app_host: $("settings-host").value.trim() || "127.0.0.1",
      app_port: Number($("settings-port").value || 8000),
      min_delay_seconds: Number($("settings-min-delay").value || 0),
      max_delay_seconds: Number($("settings-max-delay").value || 0),
      usd_krw_rate: Number($("settings-usd-krw-rate").value || 1507.2),
      crawler_mode: $("settings-crawler-mode").value || "mock",
      remember_url: $("settings-remember-url").value.trim() || providerDefaultUrl(),
      remember_cdp_url: $("settings-remember-cdp-url").value.trim() || "http://127.0.0.1:9222",
      remember_browser_port: Number($("settings-remember-browser-port").value || 9222),
      remember_browser_profile_dir: $("settings-remember-browser-profile-dir").value.trim() || "browser_profile",
      browser_locale: $("settings-browser-locale").value.trim() || "ko-KR",
      browser_accept_language: $("settings-browser-accept-language").value.trim() || "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
      browser_timezone: $("settings-browser-timezone").value.trim() || "Asia/Seoul",
      confirm_before_proposal_send: $("settings-confirm-before-proposal-send").checked,
      remember_skip_proposal_send: $("settings-remember-skip-proposal-send").checked,
      prompts: {
        match_system_prompt: $("settings-match-system-prompt").value.trim(),
        match_user_prompt: $("settings-match-user-prompt").value.trim(),
      },
    };
    const settings = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.settings = settings;
    renderSettings(settings);
    status.textContent = "저장 완료";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function showSettingsTab(tab) {
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsTab === tab);
  });
  document.querySelectorAll(".settings-panel").forEach((panel) => panel.classList.remove("active"));
  $(`settings-panel-${tab}`).classList.add("active");
}

function setModelControls(model) {
  const normalized = model.trim() || defaultOpenAIModel;
  $("settings-model").value = normalized;
  $("settings-model-preset").value = flagshipModels.includes(normalized) ? normalized : "custom";
}

function syncModelFromPreset() {
  const selected = $("settings-model-preset").value;
  if (selected !== "custom") {
    $("settings-model").value = selected;
  }
}

function connectSocket(runId) {
  if (state.socket) state.socket.close();
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws/runs/${runId}`);
  state.socket = socket;
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "state") {
      state.run = payload.state;
      renderRun(payload.state);
      if (["ready_to_send", "sending"].includes(payload.state.status) || shouldHoldForManualSend(payload.state)) {
        renderSendStage(payload.state);
        showStep(3);
        if (shouldAutoSend(payload.state)) {
          autoSendReadyRun(payload.state);
        }
      }
      if (["completed", "cancelled", "failed"].includes(payload.state.status) && !shouldHoldForManualSend(payload.state)) {
        renderSendStage(payload.state);
        renderResult(payload.state);
        showStep(4);
      }
    }
  };
  socket.onerror = () => pollRun(runId);
}

async function pollRun(runId) {
  const run = await api(`/api/runs/${runId}`);
  state.run = run;
  renderRun(run);
  if (["ready_to_send", "sending"].includes(run.status) || shouldHoldForManualSend(run)) {
    renderSendStage(run);
    showStep(3);
    if (shouldAutoSend(run)) {
      await autoSendReadyRun(run);
      return;
    }
  }
  if (["completed", "cancelled", "failed"].includes(run.status) && !shouldHoldForManualSend(run)) {
    renderSendStage(run);
    renderResult(run);
    showStep(4);
    return;
  }
  if ((run.status === "ready_to_send" && run.config?.test_mode) || shouldHoldForManualSend(run)) {
    return;
  }
  if (!["completed", "cancelled", "failed"].includes(run.status)) {
    setTimeout(() => pollRun(runId), 1500);
  }
}

async function cancelRun() {
  if (!state.run) return;
  const run = await api(`/api/runs/${state.run.run_id}/cancel`, { method: "POST", body: "{}" });
  renderRun(run);
}

async function sendSelected() {
  if (!state.run) return;
  const ids = selectedSendIds();
  if (!ids.length) return;
  await sendCandidates(ids);
}

async function autoSendReadyRun(run) {
  if (state.autoSendRunId === run.run_id) return;
  if (!shouldAutoSend(run)) return;
  state.autoSendRunId = run.run_id;
  resetSendSelection();
  renderSendStage(run);
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const ids = selectedSendIds();
  if (!ids.length) {
    const updated = await sendCandidates([]);
    if (updated) {
      renderResult(updated);
      showStep(4);
    }
    return;
  }
  await sendCandidates(ids);
}

function shouldAutoSend(run) {
  return run?.status === "ready_to_send" && run.config?.test_mode === false;
}

function shouldHoldForManualSend(run) {
  return run?.status === "completed"
    && run.config?.test_mode === true
    && run.send_threshold == null
    && Number(run.stats?.sent || 0) === 0;
}

async function sendCandidates(ids) {
  if (!state.run) return null;
  setSendControlsDisabled(true);
  try {
    const run = await api(`/api/runs/${state.run.run_id}/send-selected`, {
      method: "POST",
      body: JSON.stringify({ candidate_ids: ids, threshold: currentSendThreshold() }),
    });
    state.run = run;
    renderRun(run);
    renderSendStage(run);
    if (["completed", "cancelled", "failed"].includes(run.status)) {
      renderResult(run);
      showStep(4);
    }
    return run;
  } catch (error) {
    alert(error.message);
    return null;
  } finally {
    setSendControlsDisabled(false);
  }
}

function downloadRunSummary() {
  if (!state.run?.run_id) {
    alert("다운로드할 실행 결과가 없습니다.");
    return;
  }
  window.location.href = `/api/runs/${encodeURIComponent(state.run.run_id)}/summary`;
}

function showStep(step) {
  const changed = state.currentStep !== step;
  state.currentStep = step;
  document.querySelectorAll(".screen").forEach((screen) => screen.classList.remove("active"));
  $(`screen-${step}`).classList.add("active");
  document.querySelectorAll(".step").forEach((button) => {
    const n = Number(button.dataset.step);
    button.classList.toggle("current", n === step);
    button.classList.toggle("done", n < step);
  });
  if (changed) {
    requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));
  }
}

function renderRun(run) {
  const stats = run.stats || {};
  const total = stats.total || 0;
  const processed = stats.processed || 0;
  const crawled = stats.crawled || 0;
  const stage = run.stage || "";
  const progressProcessed = stage === "crawling" ? crawled : processed;
  const progressTotal = total || (stage === "crawling" ? (run.config?.max_candidate_count || crawled) : 0);
  const pct = progressTotal ? Math.min(100, Math.round((progressProcessed / progressTotal) * 100)) : 0;
  $("progress-count").textContent = `${progressProcessed} / ${progressTotal}`;
  const progressLabel = $("progress-count").nextElementSibling;
  if (progressLabel) {
    progressLabel.textContent = stage === "crawling" ? " 수집 중" : stage === "matching" ? " 매칭 중" : " 처리 중";
  }
  $("progress-fill").style.width = `${pct}%`;
  $("stat-total").textContent = total;
  $("stat-processed").textContent = stage === "crawling" ? crawled : processed;
  $("stat-passed").textContent = stats.passed || 0;
  $("stat-sent").textContent = stats.sent || 0;
  $("stat-excluded").textContent = stats.excluded || 0;
  $("stat-skipped").textContent = stats.skipped || 0;
  $("stat-failed").textContent = stats.failed || 0;
  $("failure-watch").textContent = `${stats.consecutive_failed || 0} / 3건`;
  $("safety-status").textContent = run.status === "failed" ? "자동 정지" : "정상";
  $("safety-status").classList.toggle("success", run.status !== "failed");
  $("logs").innerHTML = (run.logs || []).map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  renderCurrent(run);
  renderAnalysisSummary(run);
}

function renderCurrent(run) {
  const candidate = run.current_candidate;
  const latest = [...(run.results || [])].reverse().find((result) => result.match);
  if (!candidate && run.stage === "crawling") {
    const provider = providerDisplayName();
    const stats = run.stats || {};
    const crawled = stats.crawled || 0;
    const total = stats.total || run.config?.max_candidate_count || 0;
    const name = run.current_crawl_name || "";
    $("current-title").textContent = name ? `수집 중 · ${name}` : "후보자 수집 중";
    $("current-id").textContent = crawled ? `${crawled} / ${total || "-"}` : "-";
    $("current-candidate").className = "candidate-empty";
    $("current-candidate").textContent = name
      ? `${name} 후보자 정보를 ${provider}에서 수집하고 있습니다.`
      : `${provider}에서 후보자 정보를 수집하고 있습니다.`;
    return;
  }
  if (!candidate && !latest) {
    $("current-title").textContent = "후보자 대기 중";
    $("current-id").textContent = "-";
    $("current-candidate").className = "candidate-empty";
    $("current-candidate").textContent = "검색 실행 후 현재 처리 중인 더미 후보자가 표시됩니다.";
    return;
  }
  const source = latest?.candidate?.id === candidate?.id ? latest : null;
  const c = candidate || latest.candidate;
  const currentIndex = candidate ? Math.min((run.stats.processed || 0) + 1, run.stats.total || (run.stats.processed || 0) + 1) : run.stats.processed;
  $("current-title").textContent = `${currentIndex}번째 후보자 · ${c.name}`;
  $("current-id").textContent = c.id;
  $("current-candidate").className = "candidate";
  const match = source?.match;
  $("current-candidate").innerHTML = `
    <div>
      <div class="candidate-main">
        <div class="avatar">${escapeHtml(c.name.replace("O", "").slice(0, 2))}</div>
        <div>
          <h3>${escapeHtml(c.name)} <span class="small-note">· ${escapeHtml(c.role)}</span></h3>
          <div class="meta">${escapeHtml(c.company)} · ${escapeHtml(c.experience)} · ${escapeHtml(c.location)} · ${escapeHtml(c.skills.slice(0, 3).join(", "))}</div>
        </div>
      </div>
      <div class="resume-box">${escapeHtml(c.resume_text)}</div>
      <div class="reason-box">${match ? escapeHtml(match.reason) : "GPT 매칭 분석 중입니다."}</div>
    </div>
    <div>
      ${match ? `
        <div class="score-circle"><strong>${match.total_score}</strong><span>점</span></div>
      ` : `
        <div class="score-circle loading" aria-label="매칭 분석 중">
          <strong>분석 중</strong>
          <span class="loading-dots"><i></i><i></i><i></i></span>
        </div>
      `}
    </div>
  `;
}

function renderAnalysisSummary(run) {
  const threshold = run.config?.threshold ?? 90;
  const passed = (run.results || []).filter((result) => (result.match?.total_score ?? -1) >= threshold);
  $("analysis-passed-count").textContent = `${passed.length}명`;
  $("analysis-summary-list").innerHTML = passed.length ? passed.slice(0, 6).map((result) => `
    <div class="passed-item summary-only">
      <span>
        <b>${escapeHtml(result.candidate.name)}</b>
        <small>${escapeHtml(result.candidate.role)} · ${escapeHtml(result.candidate.company)} · ${result.match.total_score}점</small>
      </span>
      <span class="badge blue">통과</span>
    </div>
  `).join("") : `<div class="small-note" style="padding:16px 0">아직 통과 후보자가 없습니다.</div>`;
}

function renderSendStage(run) {
  if (!run) return;
  const threshold = currentSendThreshold();
  const rows = sortedSendRows(run, threshold);
  const eligible = rows.filter((row) => row.eligible);
  const selectable = rows.filter((row) => isSelectableSendRow(row));
  const selected = rows.filter((row) => row.selected);
  const sent = rows.filter((row) => row.result.send_status === "sent");
  const excluded = rows.filter((row) => row.result.send_status === "excluded");
  const skipped = rows.filter((row) => row.result.send_status === "skipped");
  const failed = rows.filter((row) => row.result.send_status === "failed");
  const locked = run.status === "sending" || !run.config?.test_mode;

  $("send-mode-badge").textContent = run.config?.test_mode ? "확인 후 발송" : "자동 발송";
  $("send-stage-copy").textContent = run.config?.test_mode
    ? "설정에 따라 커트라인과 체크박스를 확인한 뒤 선택 발송하세요."
    : "분석 완료 후 자동 발송이 시작됩니다.";
  $("send-summary-status").textContent = run.status === "sending" ? "발송 중" : statusText(run.status);
  $("send-total-count").textContent = rows.length;
  $("send-eligible-count").textContent = eligible.length;
  $("send-selected-count").textContent = selected.length;
  $("send-done-count").textContent = `${sent.length}${excluded.length ? ` / 제외 ${excluded.length}` : ""}${skipped.length ? ` / 스킵 ${skipped.length}` : ""}${failed.length ? ` / 실패 ${failed.length}` : ""}`;
  $("send-action-copy").textContent = run.status === "sending"
    ? "발송을 진행 중입니다."
    : `${selected.length}명 발송 대상`;
  $("send-selected-btn").disabled = locked || selected.length === 0;
  $("send-threshold").disabled = run.status === "sending" || !run.config?.test_mode;
  $("send-table-note").textContent = `${rows.length}명 중 ${eligible.length}명 기준 통과`;
  updateSendSelectAllControl(selectable, selected, locked);

  $("send-table").innerHTML = `
    <div class="send-row send-header">
      <span></span>
      ${sendSortButton("crawlOrder", "순번")}
      ${sendSortButton("name", "후보자")}
      ${sendSortButton("company", "회사")}
      ${sendSortButton("role", "직무")}
      ${sendSortButton("experience", "경력")}
      ${sendSortButton("score", "점수")}
      ${sendSortButton("status", "상태")}
      <span>사유</span>
    </div>
    ${rows.map((row) => renderSendRow(row, locked)).join("")}
  `;
  applySendThresholdVisuals();
}

function sortedSendRows(run, threshold) {
  ensureSendSelection(run, threshold);
  const rows = (run.results || []).map((result, index) => {
    const score = result.match?.total_score ?? -1;
    const eligible = score >= threshold;
    return {
      result,
      crawlOrder: Number(result.candidate.crawl_order) || index + 1,
      score,
      eligible,
      selected: Boolean(state.sendSelection[result.candidate.id])
        && eligible
        && !["sent", "skipped", "excluded"].includes(result.send_status),
    };
  });
  const { key, direction } = state.sendSort;
  const sign = direction === "asc" ? 1 : -1;
  return rows.sort((a, b) => {
    const av = sendSortValue(a, key);
    const bv = sendSortValue(b, key);
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * sign;
    return String(av).localeCompare(String(bv), "ko-KR") * sign;
  });
}

function sendSortValue(row, key) {
  const c = row.result.candidate;
  return {
    name: c.name,
    company: c.company,
    role: c.role,
    experience: c.experience,
    crawlOrder: row.crawlOrder,
    score: row.score,
    status: statusText(row.result.send_status),
  }[key] ?? "";
}

function sendSortButton(key, label) {
  const active = state.sendSort.key === key;
  const arrow = active ? (state.sendSort.direction === "asc" ? " ↑" : " ↓") : "";
  return `<button class="sort-btn" type="button" data-sort="${key}">${label}${arrow}</button>`;
}

function renderSendRow(row, locked) {
  const result = row.result;
  const candidate = result.candidate;
  const disabled = locked || !isSelectableSendRow(row);
  const classes = ["send-row"];
  if (!row.eligible) classes.push("below-threshold");
  if (result.send_status === "sent") classes.push("sent-row");
  return `
    <div class="${classes.join(" ")}" data-candidate-id="${escapeHtml(candidate.id)}" data-score="${row.score}">
      <input type="checkbox" value="${escapeHtml(candidate.id)}" ${row.selected ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <span>${row.crawlOrder}</span>
      <b>${escapeHtml(candidate.name)}</b>
      <span>${escapeHtml(candidate.company)}</span>
      <span>${escapeHtml(candidate.role)}</span>
      <span>${escapeHtml(candidate.experience)}</span>
      <b>${row.score >= 0 ? row.score : "-"}</b>
      <span>${statusText(result.send_status)}</span>
      <span>${escapeHtml(result.match?.reason || result.send_reason || "")}</span>
    </div>
  `;
}

function isSelectableSendRow(row) {
  return row.eligible && !["sent", "skipped", "excluded"].includes(row.result.send_status);
}

function updateSendThreshold(event) {
  $("send-threshold-label").textContent = event.target.value;
  resetSendSelection();
  renderSendStage(state.run);
  applySendThresholdVisuals();
}

function handleSendTableClick(event) {
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  const key = button.dataset.sort;
  if (state.sendSort.key === key) {
    state.sendSort.direction = state.sendSort.direction === "asc" ? "desc" : "asc";
  } else {
    state.sendSort = { key, direction: key === "score" ? "desc" : "asc" };
  }
  renderSendStage(state.run);
}

function handleSendTableChange(event) {
  if (event.target.type !== "checkbox") return;
  state.sendSelection[event.target.value] = event.target.checked;
  renderSendStage(state.run);
}

function handleSendSelectAllChange(event) {
  if (!state.run) return;
  const checked = event.target.checked;
  const threshold = currentSendThreshold();
  for (const row of sortedSendRows(state.run, threshold)) {
    if (isSelectableSendRow(row)) {
      state.sendSelection[row.result.candidate.id] = checked;
    }
  }
  renderSendStage(state.run);
}

function updateSendSelectAllControl(selectable, selected, locked) {
  const checkbox = $("send-select-all");
  const label = $("send-select-all-label");
  const selectedSelectableCount = selected.filter((row) => isSelectableSendRow(row)).length;
  checkbox.disabled = locked || selectable.length === 0;
  checkbox.checked = selectable.length > 0 && selectedSelectableCount === selectable.length;
  checkbox.indeterminate = selectedSelectableCount > 0 && selectedSelectableCount < selectable.length;
  label.textContent = checkbox.checked ? "전체선택해제" : "전체선택";
}

function ensureSendSelection(run, threshold) {
  for (const result of run.results || []) {
    const id = result.candidate.id;
    const eligible = (result.match?.total_score ?? -1) >= threshold;
    if (!eligible || ["sent", "skipped", "excluded"].includes(result.send_status)) {
      state.sendSelection[id] = false;
    } else if (!Object.prototype.hasOwnProperty.call(state.sendSelection, id)) {
      state.sendSelection[id] = true;
    }
  }
}

function resetSendSelection() {
  state.sendSelection = {};
}

function applySendThresholdVisuals() {
  const threshold = currentSendThreshold();
  document.querySelectorAll("#send-table .send-row[data-score]").forEach((row) => {
    const score = Number(row.dataset.score);
    const candidateId = row.dataset.candidateId;
    const checkbox = row.querySelector("input[type='checkbox']");
    const below = !Number.isFinite(score) || score < threshold;
    row.classList.toggle("below-threshold", below);
    if (below && checkbox) {
      checkbox.checked = false;
      checkbox.disabled = true;
      state.sendSelection[candidateId] = false;
    }
  });
}

function selectedSendIds() {
  return [...document.querySelectorAll("#send-table input[type='checkbox']:checked")].map((input) => input.value);
}

function currentSendThreshold() {
  return Number($("send-threshold").value || state.run?.config?.threshold || 90);
}

function setSendControlsDisabled(disabled) {
  $("send-selected-btn").disabled = disabled;
  $("send-threshold").disabled = disabled;
  $("send-select-all").disabled = disabled;
}

function renderResult(run) {
  const stats = run.stats || {};
  $("done-label").textContent = run.status === "failed" ? "이상 감지 · 자동 정지" : run.status === "cancelled" ? "사용자 중단" : "실행 완료 · 정상 종료";
  $("result-title").textContent = `${stats.total || 0}명 분석 → ${stats.selected || 0}명 발송 대상 → ${stats.sent || 0}명 발송`;
  $("result-subtitle").textContent = run.stop_reason || `${providerDisplayName()} 실행 결과`;
  $("result-total").textContent = stats.total || 0;
  $("result-passed").textContent = stats.passed || 0;
  $("result-sent").textContent = stats.sent || 0;
  $("result-excluded").textContent = stats.excluded || 0;
  $("result-skipped").textContent = stats.skipped || 0;
  $("result-failed").textContent = stats.failed || 0;
  renderCostSummary(run.usage || {});
  const rows = (run.results || []).map((result) => `
    <div class="result-row">
      <b>${escapeHtml(result.candidate.name)}</b>
      <span>${escapeHtml(result.candidate.company)} · ${escapeHtml(result.candidate.role)}</span>
      <b>${result.match ? result.match.total_score : "-"}</b>
      <span>${statusText(result.send_status)}</span>
      <span>${escapeHtml(result.match?.reason || result.send_reason || "")}</span>
    </div>
  `).join("");
  $("result-table").innerHTML = `
    <div class="result-row header"><span>후보자</span><span>현 직장 · 직무</span><span>점수</span><span>상태</span><span>사유</span></div>
    ${rows}
  `;
  const failed = (run.results || []).filter((result) => result.send_status === "failed");
  $("failure-count").textContent = `${failed.length}건`;
  $("failure-list").innerHTML = failed.length
    ? failed.map((result) => `<div class="failure">${escapeHtml(result.candidate.name)} · ${escapeHtml(result.send_reason || "발송 실패")}</div>`).join("")
    : `<div class="small-note">발송 실패가 없습니다.</div>`;
}

function renderCostSummary(usage) {
  $("result-cost-krw").textContent = usage.pricing_known
    ? formatKRW(usage.total_cost_krw || 0)
    : "단가 없음";
  $("result-token-total").textContent = formatNumber(usage.total_tokens || 0);

  const parts = [];
  if (usage.model) parts.push(`모델 ${usage.model}`);
  if (usage.pricing_known) {
    parts.push(`약 $${Number(usage.total_cost_usd || 0).toFixed(6)}`);
    parts.push(`환율 ${formatNumber(usage.usd_krw_rate || 0)}원`);
  } else if (usage.total_tokens) {
    parts.push("선택 모델 단가 미등록");
  }
  if (parts.length) {
    $("result-subtitle").textContent = `${$("result-subtitle").textContent} · ${parts.join(" · ")}`;
  }
}

function formatKRW(value) {
  return `₩${Math.round(Number(value || 0)).toLocaleString("ko-KR")}`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("ko-KR");
}

function statusText(status) {
  return {
    queued: "대기",
    running: "분석 중",
    ready_to_send: "발송 대기",
    sending: "발송 중",
    completed: "완료",
    cancelled: "중단",
    pending: "발송 대기",
    excluded: "제외",
    skipped: "발송 스킵",
    sent: "발송 완료",
    failed: "발송 실패",
  }[status] || status;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

init();
