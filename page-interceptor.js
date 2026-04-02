(function () {
  if (window.__reelsCatcherInterceptorInstalled) {
    return;
  }

  window.__reelsCatcherInterceptorInstalled = true;

  const EVENT_NAME = "reelsCatcherDM";
  const REEL_PATTERN = /instagram\.com\/(?:reel|reels|p)\/([A-Za-z0-9_-]+)/gi;

  function isRelevantRequest(url) {
    return typeof url === "string" && (url.includes("/api/v1/direct") || url.includes("/graphql"));
  }

  function normalizeUrl(shortcode) {
    return `https://www.instagram.com/reel/${shortcode}/`;
  }

  function firstString(...values) {
    for (const value of values) {
      if (value === undefined || value === null) {
        continue;
      }

      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }

      if (typeof value === "number" && Number.isFinite(value)) {
        return String(value);
      }
    }

    return null;
  }

  function nextContext(value, context) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return context;
    }

    return {
      thread_id: firstString(value.thread_id, value.thread_v2_id, context.thread_id),
      sender_id: firstString(
        value.sender_id,
        value.user_id,
        value.userId,
        value.user?.pk,
        value.user?.id,
        value.profile?.id,
        context.sender_id
      ),
      timestamp: firstString(
        value.timestamp,
        value.item_timestamp,
        value.created_at,
        value.taken_at,
        context.timestamp
      )
    };
  }

  function addMatchesFromString(text, context, output) {
    if (typeof text !== "string" || !text.includes("instagram.com/")) {
      return;
    }

    REEL_PATTERN.lastIndex = 0;

    let match;
    while ((match = REEL_PATTERN.exec(text)) !== null) {
      const shortcode = match[1];
      const key = [shortcode, context.thread_id || "", context.sender_id || "", context.timestamp || ""].join(":");

      if (output.has(key)) {
        continue;
      }

      output.set(key, {
        url: normalizeUrl(shortcode),
        shortcode,
        thread_id: context.thread_id || null,
        sender_id: context.sender_id || null,
        timestamp: context.timestamp || null
      });
    }
  }

  function extractReelUrls(payload) {
    const seen = new WeakSet();
    const output = new Map();

    function visit(value, context) {
      if (value === null || value === undefined) {
        return;
      }

      if (typeof value === "string") {
        addMatchesFromString(value, context, output);
        return;
      }

      if (typeof value !== "object") {
        return;
      }

      if (seen.has(value)) {
        return;
      }

      seen.add(value);

      if (Array.isArray(value)) {
        for (const entry of value) {
          visit(entry, context);
        }
        return;
      }

      const objectContext = nextContext(value, context);

      for (const entry of Object.values(value)) {
        visit(entry, objectContext);
      }
    }

    visit(payload, {
      thread_id: null,
      sender_id: null,
      timestamp: null
    });

    return Array.from(output.values());
  }

  function dispatchRecords(requestUrl, payload) {
    const records = extractReelUrls(payload);

    if (!records.length) {
      return;
    }

    window.dispatchEvent(
      new CustomEvent(EVENT_NAME, {
        detail: {
          requestUrl,
          detectedAt: new Date().toISOString(),
          records
        }
      })
    );
  }

  function parseJsonText(text) {
    if (typeof text !== "string" || !text.trim()) {
      return null;
    }

    try {
      return JSON.parse(text);
    } catch (error) {
      return null;
    }
  }

  function getRequestUrl(resource) {
    if (typeof resource === "string") {
      return resource;
    }

    if (resource instanceof URL) {
      return resource.toString();
    }

    if (resource && typeof resource.url === "string") {
      return resource.url;
    }

    return "";
  }

  function patchFetch() {
    const originalFetch = window.fetch;

    window.fetch = async function patchedFetch(...args) {
      const response = await originalFetch.apply(this, args);
      const requestUrl = getRequestUrl(args[0]);

      if (!isRelevantRequest(requestUrl)) {
        return response;
      }

      try {
        const clone = response.clone();
        const payload = await clone.json();
        dispatchRecords(requestUrl, payload);
      } catch (error) {
        console.debug("[reels-catcher] fetch interception skipped", error);
      }

      return response;
    };
  }

  function patchXmlHttpRequest() {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
      this.__reelsCatcherUrl = typeof url === "string" ? url : "";
      return originalOpen.call(this, method, url, ...rest);
    };

    XMLHttpRequest.prototype.send = function patchedSend(...args) {
      this.addEventListener("load", function onLoad() {
        const requestUrl = this.__reelsCatcherUrl || "";

        if (!isRelevantRequest(requestUrl)) {
          return;
        }

        try {
          const payload =
            this.responseType === "json"
              ? this.response
              : parseJsonText(typeof this.responseText === "string" ? this.responseText : "");

          if (payload) {
            dispatchRecords(requestUrl, payload);
          }
        } catch (error) {
          console.debug("[reels-catcher] xhr interception skipped", error);
        }
      });

      return originalSend.apply(this, args);
    };
  }

  // ── DOM 스캐너 (MutationObserver + WebSocket 트리거용) ───────────────
  const domSeen = new Set();

  function scanDomForReels() {
    const all = document.querySelectorAll('a[href], [href]');
    const records = [];

    all.forEach(el => {
      const href = el.getAttribute('href') || '';
      REEL_PATTERN.lastIndex = 0;
      const m = REEL_PATTERN.exec(href);
      if (m) {
        const shortcode = m[1];
        if (!domSeen.has(shortcode)) {
          domSeen.add(shortcode);
          records.push({
            url: normalizeUrl(shortcode),
            shortcode,
            thread_id: null,
            sender_id: null,
            timestamp: new Date().toISOString()
          });
        }
      }
    });

    if (records.length > 0) {
      window.dispatchEvent(new CustomEvent(EVENT_NAME, {
        detail: { requestUrl: 'dom-scan', detectedAt: new Date().toISOString(), records }
      }));
    }
  }

  // MutationObserver: DOM에 새 노드 추가될 때마다 스캔
  const observer = new MutationObserver(() => {
    scanDomForReels();
  });

  function startObserver() {
    const target = document.body || document.documentElement;
    if (target) {
      observer.observe(target, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startObserver);
  } else {
    startObserver();
  }

  // WebSocket 후킹: Instagram MQTT 메시지 수신 시 fetch 재요청 트리거
  const OrigWS = window.WebSocket;
  function PatchedWS(url, ...rest) {
    const ws = new OrigWS(url, ...rest);
    ws.addEventListener('message', (e) => {
      // 바이너리 MQTT 프레임에서 shortcode 패턴 추출 시도
      try {
        const raw = typeof e.data === 'string' ? e.data : '';
        if (raw) {
          REEL_PATTERN.lastIndex = 0;
          const m = REEL_PATTERN.exec(raw);
          if (m) {
            const shortcode = m[1];
            if (!domSeen.has(shortcode)) {
              domSeen.add(shortcode);
              window.dispatchEvent(new CustomEvent(EVENT_NAME, {
                detail: {
                  requestUrl: 'websocket',
                  detectedAt: new Date().toISOString(),
                  records: [{ url: normalizeUrl(shortcode), shortcode, thread_id: null, sender_id: null, timestamp: new Date().toISOString() }]
                }
              }));
            }
          }
        }
      } catch(e) {}
      // React 렌더링 후 DOM 재스캔
      setTimeout(scanDomForReels, 600);
      setTimeout(scanDomForReels, 2000);
    });
    return ws;
  }
  PatchedWS.prototype = OrigWS.prototype;
  PatchedWS.CONNECTING = OrigWS.CONNECTING;
  PatchedWS.OPEN = OrigWS.OPEN;
  PatchedWS.CLOSING = OrigWS.CLOSING;
  PatchedWS.CLOSED = OrigWS.CLOSED;
  window.WebSocket = PatchedWS;

  patchFetch();
  patchXmlHttpRequest();
  // 초기 DOM 스캔 (이미 로드된 메시지 처리)
  setTimeout(scanDomForReels, 1000);
})();
