const materialById = id => document.getElementById(id);
let materialSession = null;

function materialEventId(form) {
  if (!form.dataset.clientEventId) {
    const unique = globalThis.crypto?.randomUUID?.()
      || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    form.dataset.clientEventId = `material-register-${unique}`;
  }
  return form.dataset.clientEventId;
}

async function materialApi(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const response = await fetch(url, {
    ...options,
    headers:{
      "Content-Type":"application/json",
      ...(!["GET","HEAD","OPTIONS"].includes(method)
        ? {"X-CSRF-Token":materialSession?.csrf_token || ""}
        : {}),
      ...(options.headers || {})
    }
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.httpStatus = response.status;
    throw error;
  }
  return data;
}

function nextContainerCode() {
  const now = new Date();
  const parts = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
    String(now.getMilliseconds()).padStart(3, "0")
  ];
  return `RM-${parts.slice(0,3).join("")}-${parts.slice(3).join("")}`;
}

function setMaterialStatus(message, error = false) {
  const node = materialById("formStatus");
  node.textContent = message;
  node.classList.toggle("error", error);
}

async function showMaterialDetail(item) {
  const detail = await materialApi(
    `/api/material-containers/${item.id}`
  );
  materialById("materialDetailTitle").textContent =
    `${item.material_name} · ${item.container_code}`;
  const body = materialById("materialDetailBody");
  body.textContent = "";
  const summary = document.createElement("p");
  summary.className = "muted";
  summary.textContent = `供应商批号 ${item.supplier_lot || "—"} · 当前余量 ${
    item.quantity_remaining ?? "未知"
  } ${item.unit || ""} · 首次开封 ${item.opened_on || "尚未开封"}`;
  body.appendChild(summary);
  detail.events.forEach(event => {
    const card = document.createElement("div");
    card.className = "panel";
    card.style.marginTop = "10px";
    const title = document.createElement("strong");
    title.textContent = {
      registered:"登记容器",opened:"首次开封",used:"实验领用",
      adjusted:"库存调整",quarantined:"隔离",disposed:"处置"
    }[event.event_type] || event.event_type;
    const meta = document.createElement("p");
    meta.className = "muted";
    const quantity = event.quantity == null
      ? ""
      : ` · ${event.quantity} ${event.unit || ""}`;
    const batch = event.batch_id ? ` · 批次 ${event.batch_id}` : "";
    meta.textContent = `${
      new Date(event.effective_at_ms).toLocaleString("zh-CN")
    } · ${event.actor}${quantity}${batch}`;
    card.append(title, meta);
    body.appendChild(card);
  });
  materialById("materialDetailDialog").showModal();
}

async function loadMaterials() {
  const materials = await materialApi("/api/material-containers");
  const rows = materialById("materialRows");
  rows.textContent = "";
  const available = materials.filter(item => item.status === "available");
  materialById("inventorySummary").textContent =
    `${materials.length} 个容器 · ${available.length} 个可用`;
  materials.forEach(item => {
    const row = document.createElement("tr");
    row.dataset.containerCode = item.container_code;
    const values = [
      item.container_code,
      item.material_name,
      item.supplier_lot || "—",
      item.expires_on || "—",
      item.quantity_remaining == null
        ? "—"
        : `${item.quantity_remaining} ${item.unit || ""}`.trim()
    ];
    values.forEach(value => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    const status = document.createElement("td");
    status.className = item.status;
    status.textContent = {
      available:"可用",empty:"已用完",quarantined:"隔离",
      expired:"已过期",disposed:"已处置"
    }[item.status] || item.status;
    const action = document.createElement("td");
    const print = document.createElement("a");
    print.href = `/api/material-containers/${item.id}/label`;
    print.target = "_blank";
    print.rel = "noopener";
    print.textContent = "打印标签";
    const history = document.createElement("button");
    history.type = "button";
    history.textContent = "查看记录";
    history.style.marginLeft = "6px";
    history.addEventListener("click", () =>
      showMaterialDetail(item).catch(error =>
        setMaterialStatus(error.message, true)
      )
    );
    action.append(print, history);
    row.append(status, action);
    rows.appendChild(row);
  });
  if (!materials.length) {
    rows.innerHTML = '<tr><td colspan="7" class="muted">尚未登记原材料。</td></tr>';
  }
  return materials;
}

async function acceptSupplierScan(code) {
  const value = String(code || "").trim();
  if (!value) return;
  try {
    const resolved = await materialApi(
      `/api/scan/resolve?code=${encodeURIComponent(value)}`
    );
    if (resolved.kind === "material_container") {
      throw new Error(
        `该条码已登记为 ${resolved.material.container_code}`
      );
    }
  } catch (error) {
    if (error.httpStatus !== 404) throw error;
  }
  materialById("externalBarcode").value = value;
  setMaterialStatus("条码已读取，请补全本瓶信息。");
}

materialById("scanSupplierCode").addEventListener("click", () => {
  PuricoreScanner.open({onResult:acceptSupplierScan}).catch(error =>
    setMaterialStatus(error.message, true)
  );
});
materialById("cameraScanClose").addEventListener("click", async () => {
  await PuricoreScanner.stop();
  materialById("cameraScanDialog").close();
});
materialById("cameraScanManualSubmit").addEventListener(
  "click", () => PuricoreScanner.submitManual()
);
materialById("materialDetailClose").addEventListener(
  "click", () => materialById("materialDetailDialog").close()
);
materialById("refreshMaterials").addEventListener(
  "click", () => loadMaterials().catch(error =>
    setMaterialStatus(error.message, true)
  )
);
materialById("materialForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.dataset.submitting === "true") return;
  form.dataset.submitting = "true";
  if (event.submitter) event.submitter.disabled = true;
  const quantity = materialById("quantity").value;
  try {
    const created = await materialApi("/api/material-containers", {
      method:"POST",
      body:JSON.stringify({
        container_code:materialById("containerCode").value,
        external_barcode:materialById("externalBarcode").value || null,
        material_name:materialById("materialName").value,
        supplier:materialById("supplier").value || null,
        supplier_lot:materialById("supplierLot").value || null,
        expires_on:materialById("expiresOn").value || null,
        quantity_remaining:quantity === "" ? null : Number(quantity),
        unit:quantity === "" ? null : materialById("unit").value,
        client_event_id:materialEventId(form)
      })
    });
    setMaterialStatus(`已登记 ${created.container_code}，正在打开打印页。`);
    window.open(
      `/api/material-containers/${created.id}/label`,
      "_blank",
      "noopener"
    );
    event.currentTarget.reset();
    delete form.dataset.clientEventId;
    materialById("containerCode").value = nextContainerCode();
    await loadMaterials();
  } catch (error) {
    if (error.httpStatus === 403) {
      setMaterialStatus("只有实验室主管或管理员可以登记新原材料。", true);
    } else {
      setMaterialStatus(error.message, true);
    }
  } finally {
    delete form.dataset.submitting;
    if (event.submitter) event.submitter.disabled = false;
  }
});

materialById("containerCode").value = nextContainerCode();
materialApi("/api/session").then(session => {
  materialSession = session;
  return loadMaterials();
}).then(async materials => {
  const scanned = new URLSearchParams(location.search).get("scan");
  if (!scanned) return;
  const normalized = scanned.split("/scan/").pop().toUpperCase();
  const item = materials.find(
    material => material.container_code === normalized
  );
  if (!item) {
    setMaterialStatus("没有找到该原材料容器。", true);
    return;
  }
  const row = [...document.querySelectorAll("[data-container-code]")]
    .find(candidate => candidate.dataset.containerCode === normalized);
  if (row) {
    row.style.background = "rgba(105,222,192,.10)";
    row.scrollIntoView({block:"center"});
  }
  setMaterialStatus(
    `已识别 ${item.material_name} · ${item.container_code} · ${
      item.status === "available" ? "可用" : item.status
    }`
  );
  await showMaterialDetail(item);
}).catch(error => setMaterialStatus(error.message, true));
