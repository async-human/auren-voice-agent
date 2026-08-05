const sendButton = document.getElementById("send");
const status = document.getElementById("status");

sendButton.addEventListener("click", async () => {
  sendButton.disabled = true;
  status.textContent = "Reading page…";
  try {
    const result = await chrome.runtime.sendMessage({ type: "SEND_ACTIVE_TAB" });
    if (!result?.ok) {
      status.textContent = result?.error || "Could not send page.";
      return;
    }
    const truncated = result.truncated ? " (truncated)" : "";
    status.textContent =
      `Sent “${result.title}” (${result.charCount.toLocaleString()} chars)${truncated}.\n` +
      "Open Auren and ask her to explain the article.";
  } catch (error) {
    status.textContent = String(error);
  } finally {
    sendButton.disabled = false;
  }
});
