// Stock Analyst 反哺 — Background Service Worker
// 代理 fetch 请求，解决 HTTPS→HTTP mixed content 问题

const API_BASE = "http://127.0.0.1:8000/api";

// 心跳保活: 每20秒 ping，防止 Chrome MV3 休眠
setInterval(() => { console.log("[SA] worker alive"); }, 20000);

chrome.runtime.onMessage.addListener(function (request, sender, sendResponse) {
  if (request.action === "submitFeedback") {
    fetch(API_BASE + "/feedback/submit-raw", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-ID": "browser-extension" },
      body: JSON.stringify({
        ts_code: request.tsCode,
        trade_date: request.tradeDate,
        raw_response: request.text,
        source_type: "browser_extension",
      }),
    })
      .then(async function (resp) {
        if (!resp.ok) {
          var err = await resp.json().catch(function () {
            return { detail: resp.statusText };
          });
          throw new Error(err.detail || "HTTP " + resp.status);
        }
        return resp.json();
      })
      .then(function (data) {
        sendResponse({ success: true, data: data });
      })
      .catch(function (err) {
        sendResponse({ success: false, error: err.message });
      });
    return true; // 保持 sendResponse 通道开启
  }

  if (request.action === "checkHealth") {
    fetch(API_BASE + "/health")
      .then(function (r) { return r.json(); })
      .then(function (data) { sendResponse({ success: true, data: data }); })
      .catch(function () { sendResponse({ success: false }); });
    return true;
  }
});
