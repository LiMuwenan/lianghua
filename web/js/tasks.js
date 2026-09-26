// 任务页 + 日志弹窗
let currentLogTaskId = null;
let logTimer = null;

async function loadTasks() {
  const list = await API.get("/api/tasks?limit=100");
  renderTasks(list);
}

function renderTasks(list) {
  const box = $("#taskTable");
  box.innerHTML = "";
  if (!list.length) {
    const e = el("div", "empty", "暂无任务记录");
    box.appendChild(e);
    return;
  }
  const table = el("table", "table");
  const thead = el("thead");
  const tr = document.createElement("tr");
  ["ID", "名称", "类型", "状态", "退出码", "开始时间", "结束时间", "操作"].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  table.appendChild(thead);

  const tbody = el("tbody");
  list.forEach((t) => {
    const row = document.createElement("tr");
    row.appendChild(td(t.id));
    row.appendChild(td(t.ref_name));
    row.appendChild(td(t.kind));
    const badge = statusBadge(t.status);
    row.appendChild(tdAlt(`<span class="status-badge ${badge.cls}">${badge.label}</span>`));
    row.appendChild(td(t.exit_code == null ? "—" : t.exit_code));
    row.appendChild(td(fmtTime(t.started_at)));
    row.appendChild(td(fmtTime(t.finished_at)));
    const opTd = td("");
    const viewBtn = el("button", "btn ghost", "日志");
    viewBtn.onclick = () => showLog(t.id, t.ref_name, false);
    opTd.appendChild(viewBtn);
    if (t.status === "running") {
      const killBtn = el("button", "btn danger", "终止");
      killBtn.style.marginLeft = "6px";
      killBtn.onclick = () => terminateTask(t.id);
      opTd.appendChild(killBtn);
    }
    row.appendChild(opTd);
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  box.appendChild(table);
}

function td(text) {
  const c = document.createElement("td");
  c.textContent = text;
  return c;
}
function tdAlt(html) {
  const c = document.createElement("td");
  c.innerHTML = html;
  return c;
}
function fmtTime(s) {
  if (!s) return "—";
  const d = new Date(s);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
}

async function terminateTask(taskId) {
  if (!confirm(`确认终止任务 #${taskId}？`)) return;
  try {
    await API.post(`/api/tasks/${taskId}/terminate`);
    alert(`已请求终止任务 #${taskId}`);
    loadTasks();
  } catch (e) {
    alert(e.message || "终止失败");
    loadTasks();
  }
}

function showLog(taskId, name, autoRefresh) {
  currentLogTaskId = taskId;
  $("#logTaskInfo").textContent = `任务 #${taskId} ${name || ""}`;
  $("#logModal").classList.remove("hidden");
  refreshLog();
  clearInterval(logTimer);
  if (autoRefresh) {
    logTimer = setInterval(refreshLog, 2000);
  }
}

async function refreshLog() {
  if (currentLogTaskId == null) return;
  try {
    const r = await API.get(`/api/tasks/${currentLogTaskId}/log`);
    $("#logContent").textContent = r.content || "(无日志)";
    if ($("#autoTail").checked) {
      const pre = $("#logContent");
      pre.scrollTop = pre.scrollHeight;
    }
    if (["success", "failed", "partial_failed", "aborted"].includes(r.status) && logTimer) {
      clearInterval(logTimer);
      logTimer = null;
    }
  } catch (e) {
    // 任务尚未生成日志等，忽略
  }
}