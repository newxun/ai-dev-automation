(function () {
  // 后台任务页：轮询进度，完成后跳转
  var job = document.querySelector("[data-job]");
  if (job && job.querySelector(".spinner")) {
    var id = job.getAttribute("data-job");
    var label = job.querySelector(".js-label");
    var el = job.querySelector(".js-elapsed");
    var tick = function () {
      fetch("/api/job/" + id, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (el) el.textContent = d.elapsed || 0;
          if (d.label && label) label.textContent = d.label;
          if (d.status === "done" && d.url) { location.replace(d.url); return; }
          if (d.status === "error" || d.status === "missing") { location.reload(); return; }
          setTimeout(tick, 1500);
        })
        .catch(function () { setTimeout(tick, 3000); });
    };
    tick();
  }

  // 提交耗时操作时防止重复点击
  document.querySelectorAll("form.js-busy").forEach(function (f) {
    f.addEventListener("submit", function () {
      f.querySelectorAll("button").forEach(function (b) { b.disabled = true; b.textContent = "请稍候…"; });
    });
  });

  // 问导师：示例问题
  document.querySelectorAll(".js-suggest").forEach(function (b) {
    b.addEventListener("click", function () {
      var t = document.querySelector(".chat-input textarea");
      if (t) { t.value = b.textContent.trim(); t.focus(); }
    });
  });
  var end = document.getElementById("end");
  if (end && location.hash === "#end") end.scrollIntoView();

  // 设置：选服务商自动填预设
  var preset = document.querySelector(".js-preset");
  if (preset) {
    var fill = function () {
      var o = preset.options[preset.selectedIndex];
      var url = document.querySelector(".js-url"), model = document.querySelector(".js-model");
      if (url) url.placeholder = o.getAttribute("data-url") || "接口地址";
      if (model) model.placeholder = o.getAttribute("data-model") || "模型名";
      var name = preset.form.querySelector("[name=name]");
      if (name && !name.value) name.placeholder = o.textContent;
    };
    preset.addEventListener("change", fill);
    fill();
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }
})();
