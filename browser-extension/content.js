(function () {
  "use strict";

  const STOCK_RE = /\b(\d{6})\.(SH|SZ|BJ)\b/g;
  const TOAST_DURATION = 3500;
  const SCAN_INTERVAL = 1500;

  let processedMessages = new WeakSet();
  let debounceTimer = null;

  // ── 工具函数 ──────────────────────────────────

  function extractStockCodes(text) {
    var seen = new Set();
    var codes = [];
    var m;
    var re = new RegExp(STOCK_RE.source, "g");
    while ((m = re.exec(text)) !== null) {
      var full = m[1] + "." + m[2];
      if (!seen.has(full)) {
        seen.add(full);
        codes.push(full);
      }
    }
    return codes.slice(0, 8);
  }

  function showToast(msg, isError) {
    var t = document.createElement("div");
    t.className = "sa-toast " + (isError ? "error" : "success");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () {
      t.style.opacity = "0";
      t.style.transition = "opacity 0.3s";
      setTimeout(function () {
        t.remove();
      }, 300);
    }, TOAST_DURATION);
  }

  async function sendToBackend(tsCode, text) {
    // 使用本地日期（非 UTC），避免时区错配
    var d = new Date();
    var today = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    var body = JSON.stringify({
      ts_code: tsCode, trade_date: today,
      raw_response: text, source_type: "browser_extension",
    });

    // 如果扩展已失效(被刷新), 直接尝试 fetch
    if (!chrome.runtime || !chrome.runtime.id) {
      var resp = await fetch("http://127.0.0.1:8000/api/feedback/submit-raw", {
        method: "POST", headers: { "Content-Type": "application/json", "X-User-ID": "browser-extension" }, body: body,
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    }

    // 通过 service worker 代理 (解决 mixed content)
    try {
      var result = await new Promise(function (resolve, reject) {
        chrome.runtime.sendMessage({
          action: "submitFeedback", tsCode: tsCode, tradeDate: today, text: text,
        }, function (response) {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!response || !response.success) {
            reject(new Error((response && response.error) || "请求失败"));
            return;
          }
          resolve(response.data);
        });
      });
      return result;
    } catch (e) {
      // 方法2: service worker 不可用时，尝试直接 fetch
      try {
        var resp = await fetch("http://127.0.0.1:8000/api/feedback/submit-raw", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-ID": "browser-extension" },
          body: body,
        });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      } catch (e2) {
        throw new Error("无法连接后端,请确认 StockAnalyst.bat 已启动 (127.0.0.1:8000)");
      }
    }
  }

  // ── 股票代码输入对话框 ─────────────────────────

  function sendAndToast(tsCode, text) {
    var btn = document.querySelector(".sa-feedback-btn.sending");
    if (btn) { btn.disabled = true; btn.textContent = "发送中..."; }
    sendToBackend(tsCode, text).then(function () {
      showToast("✅ 已反哺: " + tsCode);
    }).catch(function (e) {
      showToast("❌ " + (e.message || "连接失败"), true);
    }).finally(function () {
      if (btn) { btn.disabled = false; btn.textContent = "📤 发送到Stock"; btn.classList.remove("sending"); }
    });
  }

  function showStockDialog(messageText) {
    var codes = extractStockCodes(messageText);

    // 只有1只股票代码 → 直接发送，无需弹窗
    if (codes.length === 1) {
      sendAndToast(codes[0], messageText);
      return;
    }

    // 多只代码或有歧义 → 弹窗确认
    var overlay = document.createElement("div");
    overlay.className = "sa-stock-dialog-overlay";

    var dialog = document.createElement("div");
    dialog.className = "sa-stock-dialog";

    var title = document.createElement("h3");
    title.textContent = "📤 发送到 Stock Analyst";

    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = "股票代码，如 000001.SZ";
    if (codes.length > 0) input.value = codes[0];

    var hint = document.createElement("div");
    hint.className = "sa-hint";
    hint.textContent = "格式: 6位数字.SH/SZ/BJ";

    var detectedDiv = document.createElement("div");
    detectedDiv.className = "sa-detected";
    if (codes.length > 0) {
      codes.forEach(function (c) {
        var chip = document.createElement("span");
        chip.className = "sa-chip";
        chip.textContent = c;
        chip.addEventListener("click", function () {
          input.value = c;
        });
        detectedDiv.appendChild(chip);
      });
    }

    var actions = document.createElement("div");
    actions.className = "sa-actions";

    var cancelBtn = document.createElement("button");
    cancelBtn.className = "sa-cancel";
    cancelBtn.textContent = "取消";
    cancelBtn.addEventListener("click", function () {
      overlay.remove();
    });

    var confirmBtn = document.createElement("button");
    confirmBtn.className = "sa-confirm";
    confirmBtn.textContent = "发送";
    confirmBtn.addEventListener("click", async function () {
      var code = input.value.trim();
      if (!/\d{6}\.(SH|SZ|BJ)/.test(code)) {
        hint.textContent = "格式错误，应为 000001.SZ";
        hint.style.color = "#f38ba8";
        return;
      }
      confirmBtn.disabled = true;
      confirmBtn.textContent = "发送中...";
      try {
        await sendToBackend(code, messageText);
        overlay.remove();
        showToast("✅ 已成功反哺到 Stock Analyst");
      } catch (e) {
        showToast("❌ " + (e.message || "连接失败，请确保后端已启动"), true);
        confirmBtn.disabled = false;
        confirmBtn.textContent = "重试";
      }
    });

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") confirmBtn.click();
      if (e.key === "Escape") overlay.remove();
    });

    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    dialog.appendChild(title);
    dialog.appendChild(input);
    dialog.appendChild(hint);
    if (codes.length > 0) dialog.appendChild(detectedDiv);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    setTimeout(function () {
      input.focus();
    }, 100);
  }

  // ── 消息检测与按钮注入 ─────────────────────────

  function findAssistantMessages() {
    // 广泛匹配 DeepSeek 的回复容器
    var candidates = document.querySelectorAll(
      '[class*="ds-markdown"], [class*="ds-message"], [class*="message"], [class*="assistant"], [class*="bot"], [class*="reply"], [class*="answer"], [class*="content"], [class*="prose"]'
    );
    var messages = [];
    candidates.forEach(function (el) {
      if (el.querySelector(".sa-feedback-btn")) return;
      var text = el.textContent || "";
      if (text.trim().length < 80) return;
      if (processedMessages.has(el)) return;
      messages.push(el);
    });
    return messages;
  }

  function injectButton(messageEl) {
    if (processedMessages.has(messageEl)) return;
    if (messageEl.querySelector(".sa-feedback-btn")) return;

    var text = messageEl.textContent || "";
    if (text.trim().length < 200) return;  // 流式小段落跳过，等完整回复后再注入

    processedMessages.add(messageEl);

    var btn = document.createElement("button");
    btn.className = "sa-feedback-btn";
    btn.textContent = "📤 发送到Stock";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      // 向上查找最顶层的对话容器（DeepSeek 的完整回复）
      var container = messageEl;
      var el = messageEl.parentElement;
      while (el && el !== document.body) {
        var text = el.textContent || "";
        if (text.length > container.textContent.length * 1.5 && text.length < 50000) {
          container = el;
        }
        el = el.parentElement;
      }
      var currentText = container.textContent || text;
      showStockDialog(currentText);
    });

    messageEl.appendChild(btn);
  }

  function scanAndInject() {
    var messages = findAssistantMessages();
    messages.forEach(injectButton);
  }

  // ── 初始化 ─────────────────────────────────────

  function init() {
    scanAndInject();
    setInterval(scanAndInject, SCAN_INTERVAL);

    // DOM变化时用防抖: 停止变化2秒后再扫描(捕获流式输出完成)
    var observer = new MutationObserver(function () {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(scanAndInject, 2000);
      // 立即也扫一次(增量内容)
      scanAndInject();
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  if (document.readyState === "complete") {
    init();
  } else {
    window.addEventListener("load", init);
  }
})();
