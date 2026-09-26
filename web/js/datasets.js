// 数据管理页
async function loadDatasets() {
  const list = await API.get("/api/datasets");
  renderDatasetChart(list);
  renderDatasetCards(list);
}

function renderDatasetChart(list) {
  const dom = $("#datasetChart");
  const chart = echarts.getInstanceByDom(dom) || echarts.init(dom);
  chart.setOption({
    tooltip: {},
    grid: { left: 40, right: 20, bottom: 30, top: 20 },
    xAxis: { type: "category", data: list.map((d) => d.name) },
    yAxis: { type: "value", name: "覆盖度", max: 1 },
    series: [
      {
        type: "bar",
        data: list.map((d) => d.coverage),
        itemStyle: { color: "#2f6fed", borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: "top", formatter: (p) => (p.value * 100).toFixed(0) + "%" },
      },
    ],
  });
}

function renderDatasetCards(list) {
  const box = $("#datasetCards");
  box.innerHTML = "";
  if (!list.length) {
    box.appendChild(el("div", "empty", "暂无数据集，请检查 config/config.yaml"));
    return;
  }
  list.forEach((d) => {
    const card = el("div", "card");
    card.appendChild(el("h3", null, d.name));
    card.appendChild(row("类型", d.type || "—"));
    card.appendChild(row("最新数据日期", d.latest_data_date || "—"));
    card.appendChild(row("滞后天数", d.lag_days == null ? "—" : `${d.lag_days} 天`));
    card.appendChild(row("文件数", d.file_count));
    const badge = statusBadge(d.status);
    card.appendChild(row("状态", el("span", `status-badge ${badge.cls}`, badge.label)));
    const runBtn = el("button", "btn ghost", `触发更新`);
    runBtn.style.marginTop = "10px";
    runBtn.onclick = () => runDataset(d);
    card.appendChild(runBtn);
    box.appendChild(card);
  });
}

function row(k, v) {
  const r = el("div", "row");
  r.appendChild(el("span", "k", k));
  r.appendChild(typeof v === "string" ? el("span", null, v) : v);
  return r;
}

async function runDataset(d) {
  try {
    await API.post(`/api/datasets/${d.id}/run`, { params: {} });
    alert(`已触发数据集「${d.name}」更新，请在「任务」页查看进度`);
  } catch (e) {
    alert("触发失败：" + e.message);
  }
}