const stationById = id => document.getElementById(id);
let stationState = {experiments:[],viscometers:[],reservations:[]};

async function stationApi(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers:{"Content-Type":"application/json",...(options.headers || {})}
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function renderStation() {
  const box = stationById("stationCards");
  box.textContent = "";
  const device = stationState.viscometers[0];
  const active = stationState.reservations.find(
    item => item.device_type === "viscometer" && item.status === "active"
  );
  stationById("stationStatus").textContent = !device
    ? "未配置粘度计"
    : active
      ? `粘度计正在用于 ${active.batch_id}`
      : "粘度计空闲，可扫描样品";
  stationState.experiments.forEach(experiment => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `<strong>${experiment.batch_id}</strong>
      <p class="muted">${experiment.membrane_system} · 操作员 ${experiment.operator}</p>
      <p class="${active?.experiment_id === experiment.id ? "busy" : "ok"}">${
        active?.experiment_id === experiment.id ? "当前正在测量" : "等待测量"
      }</p>`;
    const open = document.createElement("a");
    open.href = `/experiments/${experiment.id}`;
    open.textContent = "打开批次";
    card.appendChild(open);
    box.appendChild(card);
  });
  if (!stationState.experiments.length) {
    box.innerHTML = '<div class="card muted">目前没有处于粘度测量阶段的批次。</div>';
  }
}

async function loadStation() {
  stationState = await stationApi("/api/measurement-station");
  renderStation();
}

async function handleStationScan(code) {
  const result = await stationApi(
    `/api/scan/resolve?code=${encodeURIComponent(code)}`
  );
  if (result.kind !== "trace_item") {
    throw new Error("请扫描批次、中间品或样品标签");
  }
  const experiment = stationState.experiments.find(
    item => item.id === result.item.experiment_id
  );
  if (!experiment) throw new Error("该样品对应批次尚未进入粘度测量阶段");
  const device = stationState.viscometers[0];
  if (!device) throw new Error("系统没有可用的粘度计");
  await stationApi("/api/measurement-station/claim", {
    method:"POST",
    body:JSON.stringify({
      experiment_id:experiment.id,
      device_id:device.id
    })
  });
  location.href = `/experiments/${experiment.id}?scan=${encodeURIComponent(code)}`;
}

stationById("stationScan").addEventListener("click", () => {
  PuricoreScanner.open({onResult:handleStationScan}).catch(error =>
    stationById("stationStatus").textContent = error.message
  );
});
stationById("cameraScanClose").addEventListener("click", async () => {
  await PuricoreScanner.stop();
  stationById("cameraScanDialog").close();
});
stationById("cameraScanManualSubmit").addEventListener(
  "click", () => PuricoreScanner.submitManual()
);
stationById("stationRefresh").addEventListener("click", loadStation);
loadStation().catch(error =>
  stationById("stationStatus").textContent = error.message
);
