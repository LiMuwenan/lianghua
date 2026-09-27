// 数据管理页
async function loadDatasets() {
  const list = await API.get("/api/datasets");
  renderDatasetCards(list);
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
    card.appendChild(row("股票数(文件数)", d.file_count));
    card.appendChild(row("最新数据日期", d.latest_data_date || "—"));
    card.appendChild(row("滞后天数", d.lag_days == null ? "—" : `${d.lag_days} 天`));
    card.appendChild(row("覆盖度", d.coverage == null || d.coverage === 0 ? "—" : (d.coverage * 100).toFixed(1) + "%"));
    const badge = statusBadge(d.status);
    card.appendChild(row("状态", el("span", `status-badge ${badge.cls}`, badge.label)));

    // 起始日期输入（留空=默认：每股断点续传/空库2005-01-01） + 单个「获取数据」按钮
    const fetchRow = el("div", "fetch-row");
    fetchRow.style.marginTop = "12px";
    fetchRow.style.display = "flex";
    fetchRow.style.alignItems = "center";
    fetchRow.style.flexWrap = "wrap";
    fetchRow.style.gap = "8px";
    const lbl = el("span", "k", "起始日期");
    const input = el("input", null);
    input.type = "date";
    input.id = `startDate-${d.id}`;
    input.title = "留空=按每股已入库日期续传（空库默认 2005-01-01），已获取的日期自动跳过";
    const btn = el("button", "btn primary", "获取数据");
    const tip = el("span", "hint", "留空自动续传，已获取日期自动跳过（默认从 2005-01-01 起）");
    btn.onclick = () => runDataset(d, input.value);
    fetchRow.appendChild(lbl);
    fetchRow.appendChild(input);
    fetchRow.appendChild(btn);
    fetchRow.appendChild(tip);
    card.appendChild(fetchRow);
    box.appendChild(card);
  });
}

function row(k, v) {
  const r = el("div", "row");
  r.appendChild(el("span", "k", k));
  r.appendChild(v instanceof Node ? v : el("span", null, String(v)));
  return r;
}

async function runDataset(d, startDate) {
  try {
    const q = startDate ? `?start_date=${encodeURIComponent(startDate)}` : "";
    await API.post(`/api/datasets/${d.id}/run${q}`);
    alert(`已触发「${d.name}」获取数据${startDate ? `（从 ${startDate} 起）` : ""}，请在「任务」页查看进度`);
  } catch (e) {
    alert("触发失败：" + e.message);
  }
}