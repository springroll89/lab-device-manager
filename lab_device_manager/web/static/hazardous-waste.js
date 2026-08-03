const wasteById = id => document.getElementById(id);
const WASTE_STATUS = {
  accumulating:"收集中",
  ready_for_transfer:"已封口待转移",
  transferred:"已转移"
};
const WASTE_EVENT = {
  created:"创建容器",
  quantity_added:"加入危废",
  sealed:"封口待转移",
  transfer_registered:"登记电子联单",
  transfer_completed:"确认转移完成"
};
const wasteState = {
  session:null, locations:[], waste:[], selected:null
};

function wasteEventId(prefix) {
  return `${prefix}-${
    globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  }`;
}

function stableWasteEventId(form, prefix) {
  if (!form.dataset.clientEventId) {
    form.dataset.clientEventId = wasteEventId(prefix);
  }
  return form.dataset.clientEventId;
}

function lockWasteForm(event) {
  const form = event.currentTarget;
  if (form.dataset.submitting === "true") return null;
  form.dataset.submitting = "true";
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  return () => {
    delete form.dataset.submitting;
    if (submitter) submitter.disabled = false;
  };
}

async function wasteApi(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const response = await fetch(url, {
    ...options,
    headers:{
      Accept:"application/json",
      ...(options.body ? {"Content-Type":"application/json"} : {}),
      ...(!["GET","HEAD","OPTIONS"].includes(method)
        ? {"X-CSRF-Token":wasteState.session?.csrf_token || ""} : {}),
      ...(options.headers || {})
    }
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function canManageWaste() {
  return ["super_admin","supervisor"].includes(wasteState.session?.role);
}

function setWasteStatus(message, error = false, target = "wasteStatus") {
  const node = wasteById(target);
  node.textContent = message;
  node.classList.toggle("error", error);
}

function wasteText(tag, text, className = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function renderWaste() {
  const host = wasteById("wasteRows");
  host.textContent = "";
  wasteById("wasteCount").textContent = `共 ${wasteState.waste.length} 个容器`;
  if (!wasteState.waste.length) {
    host.appendChild(wasteText("p", "暂无危废容器记录。", "muted"));
    return;
  }
  wasteState.waste.forEach(waste => {
    const card = document.createElement("article");
    card.className = "waste-card";
    card.append(
      wasteText("h3", `${waste.waste_code} · ${waste.waste_name}`),
      wasteText(
        "p",
        `${waste.quantity} ${waste.unit} · ${waste.location_name}`,
        "muted"
      ),
      wasteText(
        "span",
        WASTE_STATUS[waste.status] || waste.status,
        `badge ${waste.status}`
      )
    );
    card.addEventListener("click", () =>
      openWasteDetail(waste.id).catch(error =>
        setWasteStatus(error.message, true)
      )
    );
    host.appendChild(card);
  });
}

async function loadWaste() {
  setWasteStatus("正在读取危废台账…");
  const status = wasteById("wasteStatusFilter").value;
  const [session,locations,result] = await Promise.all([
    wasteApi("/api/session"),
    wasteApi("/api/storage-locations"),
    wasteApi(
      `/api/inventory/hazardous-waste${
        status ? `?status=${encodeURIComponent(status)}` : ""
      }`
    )
  ]);
  wasteState.session = session;
  wasteState.locations = locations.filter(
    location => location.location_type === "waste_storage"
  );
  wasteState.waste = result.waste;
  wasteById("exportWasteLedger").href =
    `/api/inventory/hazardous-waste.csv${
      status ? `?status=${encodeURIComponent(status)}` : ""
    }`;
  wasteById("openWasteForm").hidden = !canManageWaste();
  renderWaste();
  setWasteStatus("危废台账已更新");
}

function renderWasteCharacteristics() {
  const host = wasteById("wasteCharacteristics");
  host.textContent = "";
  ["腐蚀性","毒性","易燃性","反应性","感染性"].forEach(value => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    label.append(input, document.createTextNode(value));
    host.appendChild(label);
  });
}

function resetWasteForm() {
  delete wasteById("wasteForm").dataset.clientEventId;
  wasteById("wasteForm").reset();
  renderWasteCharacteristics();
  const select = wasteById("wasteLocation");
  select.textContent = "";
  wasteState.locations.forEach(location => {
    const option = document.createElement("option");
    option.value = location.location_code;
    option.textContent = `${location.display_name}（${location.location_code}）`;
    select.appendChild(option);
  });
  setWasteStatus("", false, "wasteFormStatus");
}

async function submitWasteForm(event) {
  event.preventDefault();
  const unlock = lockWasteForm(event);
  if (!unlock) return;
  try {
    const created = await wasteApi("/api/inventory/hazardous-waste", {
      method:"POST",
      body:JSON.stringify({
        waste_name:wasteById("wasteName").value,
        waste_category_code:wasteById("wasteCategoryCode").value,
        waste_category_name:wasteById("wasteCategoryName").value || null,
        physical_state:wasteById("wastePhysicalState").value,
        hazard_characteristics:[
          ...wasteById("wasteCharacteristics")
            .querySelectorAll("input:checked")
        ].map(input => input.value),
        composition:wasteById("wasteComposition").value,
        quantity:Number(wasteById("wasteQuantity").value),
        unit:wasteById("wasteUnit").value,
        package_type:wasteById("wastePackage").value,
        location_code:wasteById("wasteLocation").value,
        note:wasteById("wasteNote").value || null,
        client_event_id:stableWasteEventId(
          event.currentTarget, "waste-create"
        )
      })
    });
    wasteById("wasteFormDialog").close();
    delete event.currentTarget.dataset.clientEventId;
    await loadWaste();
    await openWasteDetail(created.id);
  } catch (error) {
    setWasteStatus(error.message, true, "wasteFormStatus");
  } finally {
    unlock();
  }
}

function detailValue(host, label, value) {
  const node = document.createElement("div");
  node.className = "detail-value";
  node.append(
    wasteText("span", label),
    wasteText("strong", String(value || "—"))
  );
  host.appendChild(node);
}

async function openWasteDetail(wasteId) {
  const waste = await wasteApi(
    `/api/inventory/hazardous-waste/${wasteId}`
  );
  wasteState.selected = waste;
  wasteById("wasteDetailTitle").textContent =
    `${waste.waste_code} · ${waste.waste_name}`;
  const body = wasteById("wasteDetailBody");
  body.textContent = "";
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  [
    ["状态",WASTE_STATUS[waste.status] || waste.status],
    ["当前数量",`${waste.quantity} ${waste.unit}`],
    ["危废类别",`${waste.waste_category_code} ${
      waste.waste_category_name || ""
    }`],
    ["危险特性",(waste.hazard_characteristics || []).join(" / ")],
    ["主要成分",waste.composition],
    ["包装容器",waste.package_type],
    ["暂存库位",`${waste.location_name}（${waste.location_code}）`],
    ["创建人",waste.created_by]
  ].forEach(([label,value]) => detailValue(grid,label,value));
  body.appendChild(grid);
  const actions = document.createElement("div");
  actions.className = "toolbar";
  actions.style.marginTop = "14px";
  if (waste.status === "accumulating") {
    actions.appendChild(wasteActionButton("add","加入数量","primary"));
    if (canManageWaste()) {
      actions.appendChild(wasteActionButton("seal","封口待转移","danger"));
    }
  }
  if (waste.status === "ready_for_transfer" && canManageWaste()) {
    if (!waste.transfers.length) {
      actions.appendChild(
        wasteActionButton("register_transfer","登记国家电子联单","primary")
      );
    } else if (waste.transfers[0].status === "registered") {
      actions.appendChild(
        wasteActionButton("complete_transfer","确认已交接","danger")
      );
    }
  }
  body.appendChild(actions);
  body.appendChild(wasteText("h3", "全过程记录"));
  waste.events.forEach(event => {
    const row = document.createElement("div");
    row.className = "event";
    row.append(
      wasteText("strong", WASTE_EVENT[event.event_type] || event.event_type),
      wasteText(
        "div",
        `${new Date(event.effective_at_ms).toLocaleString("zh-CN")} · ${
          event.actor
        } · 数量 ${event.quantity_after} ${waste.unit}`,
        "muted"
      )
    );
    body.appendChild(row);
  });
  wasteById("wasteDetailDialog").showModal();
}

function wasteActionButton(action, label, tone) {
  const button = wasteText("button", label, `btn ${tone}`);
  button.type = "button";
  button.addEventListener("click", () => openWasteAction(action,label));
  return button;
}

function openWasteAction(action, label) {
  delete wasteById("wasteActionForm").dataset.clientEventId;
  wasteById("wasteActionForm").reset();
  wasteById("wasteActionId").value = wasteState.selected.id;
  wasteById("wasteActionType").value = action;
  wasteById("wasteActionTitle").textContent =
    `${label} · ${wasteState.selected.waste_code}`;
  wasteById("wasteAddFields").hidden = action !== "add";
  wasteById("wasteTransferFields").hidden =
    action !== "register_transfer";
  setWasteStatus("", false, "wasteActionStatus");
  wasteById("wasteDetailDialog").close();
  wasteById("wasteActionDialog").showModal();
}

async function submitWasteAction(event) {
  event.preventDefault();
  const unlock = lockWasteForm(event);
  if (!unlock) return;
  const wasteId = wasteById("wasteActionId").value;
  const action = wasteById("wasteActionType").value;
  let url = `/api/inventory/hazardous-waste/${wasteId}/events`;
  let payload = {
    action,
    client_event_id:stableWasteEventId(
      event.currentTarget, `waste-${action}-${wasteId}`
    )
  };
  if (action === "add") {
    payload.quantity = Number(wasteById("wasteAddQuantity").value);
  } else if (action === "register_transfer") {
    url = `/api/inventory/hazardous-waste/${wasteId}/transfers`;
    payload = {
      national_manifest_no:wasteById("manifestNo").value,
      national_system_ref:wasteById("manifestRef").value || null,
      transfer_date:wasteById("transferDate").value,
      transporter_name:wasteById("transporterName").value,
      transporter_license_no:wasteById("transporterLicense").value,
      vehicle_no:wasteById("vehicleNo").value || null,
      recipient_name:wasteById("recipientName").value,
      recipient_permit_no:wasteById("recipientPermit").value,
      disposal_method:wasteById("disposalMethod").value,
      client_event_id:stableWasteEventId(
        event.currentTarget, `waste-transfer-${wasteId}`
      )
    };
  }
  try {
    const updated = await wasteApi(url, {
      method:"POST", body:JSON.stringify(payload)
    });
    wasteById("wasteActionDialog").close();
    delete event.currentTarget.dataset.clientEventId;
    await loadWaste();
    await openWasteDetail(updated.id);
  } catch (error) {
    setWasteStatus(error.message, true, "wasteActionStatus");
  } finally {
    unlock();
  }
}

function wireWasteEvents() {
  wasteById("openWasteForm").addEventListener("click", () => {
    resetWasteForm();
    if (!wasteState.locations.length) {
      setWasteStatus(
        "请先在“物品与库存”中配置类型为“危废暂存区”的库位",
        true,
        "wasteFormStatus"
      );
    }
    wasteById("wasteFormDialog").showModal();
  });
  wasteById("wasteForm").addEventListener("submit", submitWasteForm);
  wasteById("wasteActionForm").addEventListener(
    "submit", submitWasteAction
  );
  wasteById("refreshWaste").addEventListener(
    "click", () => loadWaste().catch(error =>
      setWasteStatus(error.message, true)
    )
  );
  wasteById("wasteStatusFilter").addEventListener(
    "change", () => loadWaste().catch(error =>
      setWasteStatus(error.message, true)
    )
  );
  document.querySelectorAll("[data-close]").forEach(button => {
    button.addEventListener("click", () =>
      wasteById(button.dataset.close).close()
    );
  });
}

renderWasteCharacteristics();
wireWasteEvents();
loadWaste().catch(error => setWasteStatus(error.message, true));
