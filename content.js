const STORAGE_KEYS = {
  enabled: "enabled"
};

const REEL_PATTERN = /instagram\.com\/(?:reel|reels|p)\/([A-Za-z0-9_-]+)/i;
const sessionSeen = new Set();
let extensionEnabled = true;
let interceptorInjected = false;

function storageGet(keys) {
  return new Promise((resolve) => {
    chrome.storage.local.get(keys, resolve);
  });
}

function storageSet(values) {
  return new Promise((resolve) => {
    chrome.storage.local.set(values, resolve);
  });
}

function ensureDefaults() {
  return storageGet([STORAGE_KEYS.enabled]).then((stored) => {
    if (typeof stored.enabled === "boolean") {
      extensionEnabled = stored.enabled;
      return;
    }

    extensionEnabled = true;
    return storageSet({ enabled: true });
  });
}

function extractShortcode(url) {
  const match = typeof url === "string" ? url.match(REEL_PATTERN) : null;
  return match ? match[1] : null;
}

function injectPageInterceptor() {
  // manifest의 world:MAIN content_script로 직접 주입되므로 별도 inject 불필요
  interceptorInjected = true;
}

function buildMessage(record, detectedAt) {
  const shortcode = record.shortcode || extractShortcode(record.url);
  if (!shortcode) {
    return null;
  }

  const dedupeKey = [shortcode, record.thread_id || "", record.sender_id || "", record.timestamp || ""].join(":");
  if (sessionSeen.has(dedupeKey)) {
    return null;
  }

  sessionSeen.add(dedupeKey);

  return {
    type: "NEW_REEL",
    url: record.url,
    shortcode,
    thread_id: record.thread_id || null,
    sender_id: record.sender_id || null,
    timestamp: record.timestamp || detectedAt || new Date().toISOString()
  };
}

function handleReelsEvent(event) {
  if (!extensionEnabled) {
    return;
  }

  const detail = event.detail || {};
  const records = Array.isArray(detail.records) ? detail.records : [];

  for (const record of records) {
    const message = buildMessage(record, detail.detectedAt);
    if (!message) {
      continue;
    }

    chrome.runtime.sendMessage(message, () => {
      if (chrome.runtime.lastError) {
        console.debug("[reels-catcher] sendMessage failed", chrome.runtime.lastError.message);
      }
    });
  }
}

async function init() {
  window.addEventListener("reelsCatcherDM", handleReelsEvent);
  await ensureDefaults();
  injectPageInterceptor();
}

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.enabled) {
    return;
  }

  extensionEnabled = Boolean(changes.enabled.newValue);

  if (extensionEnabled) {
    injectPageInterceptor();
  }
});

init().catch((error) => {
  console.error("[reels-catcher] content init failed", error);
});
