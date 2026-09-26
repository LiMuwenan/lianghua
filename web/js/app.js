// 应用入口：Tab 切换、事件绑定、轮询运行状态
function switchPage(name) {
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`page-${name}`).classList.remove("hidden");
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.page === name;
    t.classList.toggle("active", on);
  });
  ({ datasets: loadDatasets, strategies: loadStrategies, tasks: loadTasks })[name]();
}

async function pollRunning() {
  try {
    const r = await API.get("/api/tasks/running");
    $("#runningHint").textContent = r.task_id ? `⏳ 正在运行任务 #${r.task_id}` : "";
  } catch (e) {
    $("#runningHint").textContent = "";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => switchPage(t.dataset.page);
  });

  // 页面动作
  $("#refreshDatasets").onclick = loadDatasets;
  $("#rescan").onclick = async () => {
    await API.post("/api/strategies/scan");
    await loadStrategies();
  };
  $("#refreshTasks").onclick = loadTasks;

  // 日志弹窗
  $("#closeLog").onclick = () => {
    $("#logModal").classList.add("hidden");
    clearInterval(logTimer);
    currentLogTaskId = null;
  };
  $("#refreshLog").onclick = refreshLog;

  // 首次加载 + 状态轮询
  switchPage("datasets");
  setInterval(pollRunning, 3000);
});