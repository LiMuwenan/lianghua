// REST API 封装
const API = {
  get: async (url) => (await fetch(url)).json(),
  post: async (url, body = {}) => {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    return r.json();
  },
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function statusBadge(status) {
  const cls =
    status === "success" || status === "正常"
      ? "status-ok"
      : status === "queued" || status === "running"
      ? "status-warn"
      : status === "failed" || status === "部分失败" || status === "缺失"
      ? "status-err"
      : "status-muted";
  const label = status === "running" ? "运行中" : status === "queued" ? "排队中" : status;
  return { cls, label };
}