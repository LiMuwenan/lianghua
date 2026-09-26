// 策略/回测页
async function loadStrategies() {
  const list = await API.get("/api/strategies");
  renderStrategies(list);
}

function renderStrategies(list) {
  const box = $("#strategyList");
  box.innerHTML = "";
  if (!list.length) {
    box.appendChild(el("div", "empty", "暂无已登记脚本，点击右上角「重新扫描登记」（需存在 *.py.manifest.yaml）"));
    return;
  }
  list.forEach((s) => box.appendChild(strategyCard(s)));
}

function strategyCard(s) {
  const card = el("div", "strategy-card");

  const head = el("div", "sc-head");
  head.appendChild(el("h3", null, s.name));
  const kindMap = { script: "数据脚本", strategy: "策略", backtest: "回测" };
  head.appendChild(el("span", "kind-tag", kindMap[s.kind] || s.kind));
  card.appendChild(head);

  card.appendChild(el("div", "path", s.script_path));
  if (s.data_dep) card.appendChild(el("div", "path", `依赖数据: ${s.data_dep}`));

  // 参数表单
  const paramsBox = el("div", "sc-params");
  const inputs = {};
  const schema = s.params_schema || {};
  const keys = Object.keys(schema);
  if (keys.length) {
    keys.forEach((k) => {
      const meta = schema[k] || {};
      paramsBox.appendChild(el("label", null, `${meta.desc || k} (${k})`));
      const inp = el("input");
      inp.value = meta.default != null ? String(meta.default) : "";
      inp.dataset.type = meta.type || "text";
      inp.dataset.var = k;
      if (meta.desc && meta.desc !== k) paramsBox.appendChild(el("div", "p-desc", meta.desc));
      paramsBox.appendChild(inp);
      inputs[k] = inp;
    });
  } else {
    paramsBox.appendChild(el("div", "p-desc", "该脚本无参数"));
  }
  card.appendChild(paramsBox);

  const runBtn = el("button", "btn", paramsBox.querySelector("input") ? "运行（当前参数）" : "运行");
  runBtn.style.width = "100%";
  runBtn.onclick = () => runStrategy(s, inputs);
  card.appendChild(runBtn);
  return card;
}

async function runStrategy(s, inputs) {
  const params = {};
  for (const k in inputs) params[k] = parseParam(inputs[k].value, inputs[k].dataset.type);
  try {
    const t = await API.post(`/api/strategies/${s.id}/run`, { params });
    showLog(t.id, t.ref_name, true);
  } catch (e) {
    alert("触发失败：" + e.message);
  }
}

function parseParam(raw, type) {
  if (raw === "") return "";
  if (type === "int") return parseInt(raw);
  if (type === "float") return parseFloat(raw);
  if (type === "bool") return raw === "true" || raw === "True" || raw === "1";
  if (Array.isArray(type) || String(raw).startsWith("[")) {
    try {
      const arr = JSON.parse(raw.replace(/'/g, '"'));
      return Array.isArray(arr) ? arr : raw;
    } catch {
      return raw;
    }
  }
  return raw;
}