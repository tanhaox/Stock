(function () {
  var dot = document.getElementById("dot");
  var text = document.getElementById("status-text");

  fetch("http://127.0.0.1:8000/api/health")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.status === "ok") {
        dot.className = "dot on";
        text.textContent = "后端在线 (v" + data.version + ")";
      } else {
        throw new Error("unhealthy");
      }
    })
    .catch(function () {
      dot.className = "dot off";
      text.textContent = "后端离线 — 请启动 StockAnalyst.bat";
    });
})();
