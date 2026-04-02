const STORAGE_KEYS = {
  enabled: "enabled",
  todayProcessedCount: "todayProcessedCount",
  processedCountDate: "processedCountDate"
};

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

function getTodayKey() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function render(state) {
  const toggle = document.getElementById("enabledToggle");
  const statusText = document.getElementById("statusText");
  const todayCount = document.getElementById("todayCount");

  toggle.checked = state.enabled;
  statusText.textContent = state.enabled ? "감시 중" : "비활성";
  statusText.dataset.state = state.enabled ? "on" : "off";
  todayCount.textContent = String(state.todayProcessedCount);
}

async function loadState() {
  const today = getTodayKey();
  const stored = await storageGet([
    STORAGE_KEYS.enabled,
    STORAGE_KEYS.todayProcessedCount,
    STORAGE_KEYS.processedCountDate
  ]);

  const enabled = stored.enabled === true;
  const count = stored.processedCountDate === today && typeof stored.todayProcessedCount === "number"
    ? stored.todayProcessedCount
    : 0;

  render({
    enabled,
    todayProcessedCount: count
  });
}

async function init() {
  await loadState();

  const toggle = document.getElementById("enabledToggle");
  toggle.addEventListener("change", async () => {
    await storageSet({ enabled: toggle.checked });
    render({
      enabled: toggle.checked,
      todayProcessedCount: Number(document.getElementById("todayCount").textContent) || 0
    });
  });
}

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") {
    return;
  }

  if (changes.enabled || changes.todayProcessedCount || changes.processedCountDate) {
    loadState().catch((error) => {
      console.error("[reels-catcher] popup refresh failed", error);
    });
  }
});

async function checkServer() {
  const dot = document.getElementById("serverDot");
  const text = document.getElementById("serverStatus");
  try {
    const res = await fetch("http://localhost:8000/api/reels", {
      method: "OPTIONS",
      signal: AbortSignal.timeout(2000)
    });
    dot.style.background = "#2d7f4e";
    text.textContent = "연결됨";
    text.dataset.state = "on";
  } catch {
    dot.style.background = "#ef6b3f";
    text.textContent = "서버 꺼짐";
    text.dataset.state = "off";
  }
}

init().catch((error) => {
  console.error("[reels-catcher] popup init failed", error);
});

checkServer();
setInterval(checkServer, 5000);
