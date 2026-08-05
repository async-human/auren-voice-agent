const DEFAULT_API = "http://127.0.0.1:8081";
const MAX_CHARS = 100000;

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.sync.get(["apiBaseUrl"]);
  if (!current.apiBaseUrl) {
    await chrome.storage.sync.set({ apiBaseUrl: DEFAULT_API });
  }
});

chrome.commands.onCommand.addListener((command) => {
  if (command === "send-page-to-auren") {
    sendActiveTab().catch((error) => console.error(error));
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "SEND_ACTIVE_TAB") {
    sendActiveTab()
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  return false;
});

async function sendActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    return { ok: false, error: "No active tab." };
  }
  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
    return { ok: false, error: "This page cannot be read by the extension." };
  }

  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["extract.js"],
  });

  if (!result?.text || result.text.trim().length < 40) {
    return {
      ok: false,
      error: "Could not extract enough readable text from this page.",
      title: result?.title,
      url: result?.url,
    };
  }

  let text = result.text.trim();
  let truncated = false;
  if (text.length > MAX_CHARS) {
    text = `${text.slice(0, MAX_CHARS - 1)}…`;
    truncated = true;
  }

  const settings = await chrome.storage.sync.get(["apiBaseUrl", "bearerToken"]);
  const apiBaseUrl = (settings.apiBaseUrl || DEFAULT_API).replace(/\/$/, "");
  const headers = { "Content-Type": "application/json" };
  if (settings.bearerToken) {
    headers.Authorization = `Bearer ${settings.bearerToken}`;
  }

  const response = await fetch(`${apiBaseUrl}/v1/page-context`, {
    method: "PUT",
    headers,
    body: JSON.stringify({
      url: result.url,
      title: result.title,
      text,
      source: "extension",
    }),
  });

  if (!response.ok) {
    const detail = await response.text();
    return {
      ok: false,
      error: `API ${response.status}: ${detail.slice(0, 240)}`,
    };
  }

  const body = await response.json();
  return {
    ok: true,
    title: body.title || result.title,
    url: body.url || result.url,
    charCount: body.char_count || text.length,
    truncated: Boolean(body.truncated || truncated),
  };
}
