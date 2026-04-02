// ── reels-catcher background.js (chrome.debugger 방식) ───────────────────────
const DEFAULT_API_ENDPOINT = "http://localhost:8000/api/reels";
const REEL_PATTERN = /instagram\.com\/(?:reel|reels|p)\/([A-Za-z0-9_-]+)/gi;
const DM_URL_PATTERN = /direct_v2|direct\/inbox|direct\/threads/;

const attachedTabs = new Set();
const inFlightShortcodes = new Set();

// ── storage helpers ───────────────────────────────────────────────────────────
function storageGet(keys) {
  return new Promise(r => chrome.storage.local.get(keys, r));
}
function storageSet(values) {
  return new Promise(r => chrome.storage.local.set(values, r));
}
function getTodayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

async function isProcessed(shortcode) {
  const { processedShortcodes = [] } = await storageGet(['processedShortcodes']);
  return processedShortcodes.some(e => e.shortcode === shortcode);
}

async function markProcessed(shortcode) {
  const today = getTodayKey();
  const { processedShortcodes = [], todayProcessedCount = 0, processedCountDate } = await storageGet(['processedShortcodes','todayProcessedCount','processedCountDate']);
  const next = [...processedShortcodes.slice(-999), { shortcode, processedAt: Date.now() }];
  const count = processedCountDate === today ? todayProcessedCount + 1 : 1;
  await storageSet({ processedShortcodes: next, todayProcessedCount: count, processedCountDate: today });
}

// ── 릴스 URL 추출 ─────────────────────────────────────────────────────────────
function extractReels(text, threadId) {
  if (!text || typeof text !== 'string') return [];
  const results = [];
  const seen = new Set();
  REEL_PATTERN.lastIndex = 0;
  let m;
  while ((m = REEL_PATTERN.exec(text)) !== null) {
    const sc = m[1];
    if (!seen.has(sc)) {
      seen.add(sc);
      results.push({
        url: `https://www.instagram.com/reel/${sc}/`,
        shortcode: sc,
        thread_id: threadId || null,
        timestamp: new Date().toISOString(),
        source: 'chrome-extension'
      });
    }
  }
  return results;
}

function extractReelsFromJson(obj, threadId, depth = 0) {
  if (depth > 10 || !obj) return [];
  if (typeof obj === 'string') return extractReels(obj, threadId);
  if (Array.isArray(obj)) return obj.flatMap(v => extractReelsFromJson(v, threadId, depth + 1));
  if (typeof obj === 'object') {
    const tid = obj.thread_id || obj.thread_v2_id || threadId;
    return Object.values(obj).flatMap(v => extractReelsFromJson(v, tid, depth + 1));
  }
  return [];
}

// ── 로컬 서버 전송 ────────────────────────────────────────────────────────────
async function forwardReel(reel) {
  const { enabled = true, apiBaseUrl = DEFAULT_API_ENDPOINT } = await storageGet(['enabled','apiBaseUrl']);
  if (!enabled) return;
  if (await isProcessed(reel.shortcode)) return;
  if (inFlightShortcodes.has(reel.shortcode)) return;

  inFlightShortcodes.add(reel.shortcode);
  try {
    const res = await fetch(apiBaseUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reel)
    });
    if (res.ok) {
      await markProcessed(reel.shortcode);
      console.log('[reels-catcher] ✅ 전송:', reel.shortcode);
    }
  } catch (e) {
    console.warn('[reels-catcher] 서버 전송 실패:', e.message);
  } finally {
    inFlightShortcodes.delete(reel.shortcode);
  }
}

// ── debugger 붙이기 ───────────────────────────────────────────────────────────
async function attachDebugger(tabId) {
  if (attachedTabs.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, '1.3');
    await chrome.debugger.sendCommand({ tabId }, 'Network.enable', {});
    // WebSocket 프레임 캡처 활성화
    await chrome.debugger.sendCommand({ tabId }, 'Network.setMonitoringXHREnabled', { enabled: true }).catch(() => {});
    attachedTabs.add(tabId);
    console.log('[reels-catcher] debugger attached to tab', tabId);
  } catch (e) {
    console.warn('[reels-catcher] debugger attach failed:', e.message);
  }
}

function detachDebugger(tabId) {
  if (!attachedTabs.has(tabId)) return;
  chrome.debugger.detach({ tabId }).catch(() => {});
  attachedTabs.delete(tabId);
}

// ── CDP Network 이벤트 처리 ───────────────────────────────────────────────────
chrome.debugger.onEvent.addListener(async (source, method, params) => {
  const tabId = source.tabId;



  // ── HTTP 응답 본문 ──────────────────────────────────────────────────────
  if (method === 'Network.responseReceived') {
    const url = params?.response?.url || '';
    if (!DM_URL_PATTERN.test(url)) return;

    try {
      const result = await chrome.debugger.sendCommand(
        { tabId }, 'Network.getResponseBody', { requestId: params.requestId }
      );
      const body = result?.body;
      if (!body) return;
      let json;
      try { json = JSON.parse(body); } catch { return; }
      const reels = extractReelsFromJson(json, null);

      for (const reel of reels) await forwardReel(reel);
    } catch {}
    return;
  }

  // ── WebSocket 프레임 (MQTT over WS, base64 바이너리) ────────────────────
  if (method === 'Network.webSocketFrameReceived') {
    const frame = params?.response;
    if (!frame) return;
    const opcode = frame.opcode; // 1=text, 2=binary
    const payloadData = frame.payloadData || '';

    let decoded = payloadData;

    // binary frame → base64 디코드 → UTF-8 string
    if (opcode === 2) {
      try {
        const binary = atob(payloadData);
        decoded = binary;
      } catch {}
    }

    // 디코드된 문자열에서 Instagram URL 추출
    const reels = extractReels(decoded, null);
    if (reels.length > 0) {
      console.log('[reels-catcher] WS reel 감지:', reels.map(r=>r.shortcode));
      for (const reel of reels) await forwardReel(reel);
      return;
    }

    // JSON 시도
    try {
      const json = JSON.parse(decoded);
      const reels2 = extractReelsFromJson(json, null);
      if (reels2.length > 0) {
        console.log('[reels-catcher] WS JSON reel 감지:', reels2.map(r=>r.shortcode));
        for (const reel of reels2) await forwardReel(reel);
      }
    } catch {}
  }
});

// ── 탭 감지: Instagram DM 탭에 자동 attach ───────────────────────────────────
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== 'complete') return;
  const url = tab.url || '';
  if (url.includes('instagram.com/direct')) {
    const { enabled = true } = await storageGet(['enabled']);
    if (enabled) await attachDebugger(tabId);
  } else if (attachedTabs.has(tabId)) {
    detachDebugger(tabId);
  }
});

chrome.tabs.onRemoved.addListener(tabId => {
  detachDebugger(tabId);
});

// ── debugger 강제 해제 감지 ───────────────────────────────────────────────────
chrome.debugger.onDetach.addListener((source) => {
  attachedTabs.delete(source.tabId);
  console.log('[reels-catcher] debugger detached from tab', source.tabId);
});

// ── popup에서 토글 변경 시 ────────────────────────────────────────────────────
chrome.storage.onChanged.addListener(async (changes) => {
  if (!changes.enabled) return;
  const enabled = changes.enabled.newValue;
  if (!enabled) {
    for (const tabId of attachedTabs) detachDebugger(tabId);
  } else {
    const tabs = await chrome.tabs.query({ url: '*://www.instagram.com/direct/*' });
    for (const tab of tabs) await attachDebugger(tab.id);
  }
});

// ── 시작 시 이미 열린 DM 탭 감지 ─────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(async () => {
  await storageSet({ enabled: false, apiBaseUrl: DEFAULT_API_ENDPOINT });
  // 기본값 비활성 — 사용자가 팝업에서 직접 켜야 함
});

chrome.runtime.onStartup.addListener(async () => {
  const { enabled = false } = await storageGet(['enabled']);
  if (!enabled) return;
  const tabs = await chrome.tabs.query({ url: '*://www.instagram.com/direct/*' });
  for (const tab of tabs) await attachDebugger(tab.id);
});
