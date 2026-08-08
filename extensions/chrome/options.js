const apiBaseUrl = document.getElementById("apiBaseUrl");
const bearerToken = document.getElementById("bearerToken");
const status = document.getElementById("status");
const save = document.getElementById("save");

chrome.storage.sync.get(["apiBaseUrl", "bearerToken"]).then((values) => {
  apiBaseUrl.value = values.apiBaseUrl || "http://127.0.0.1:8080";
  bearerToken.value = values.bearerToken || "";
});

save.addEventListener("click", async () => {
  await chrome.storage.sync.set({
    apiBaseUrl: apiBaseUrl.value.trim().replace(/\/$/, ""),
    bearerToken: bearerToken.value.trim(),
  });
  status.textContent = "Saved.";
});
