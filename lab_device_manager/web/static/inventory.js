const inventoryById = id => document.getElementById(id);

const CATEGORY_LABELS = {
  chemical:"化学品", consumable:"耗材", office:"办公用品"
};
const STATUS_LABELS = {
  available:"可用", empty:"已用完", quarantined:"待处置/隔离",
  expired:"已过期", disposed:"已处置"
};
const ACTION_LABELS = {
  registered:"登记", received:"入库", issued:"领用", adjusted:"盘点",
  experiment_used:"实验领用", opened:"开封", quarantined:"隔离",
  disposed:"处置", imported:"历史迁移"
};
const HAZARDS = [
  "易燃","易爆","氧化","有毒","腐蚀","反应性","有害/刺激","环境危害"
];
const CONTROL_CATEGORIES = [
  "易制爆","易制毒第一类","易制毒第二类","易制毒第三类",
  "剧毒","重大危险源","药品类易制毒","内部受控"
];
const STORAGE_GROUP_LABELS = {
  unassessed:"未判定", general_chemical:"一般化学品", flammable:"易燃品",
  oxidizer:"氧化剂", acid:"酸", alkali:"碱", toxic:"有毒品",
  water_reactive:"遇水反应", pyrophoric:"自燃品",
  compressed_gas:"压缩气体", refrigerated:"冷藏品"
};
const GHS_PICTOGRAMS = [
  "GHS01","GHS02","GHS03","GHS04","GHS05",
  "GHS06","GHS07","GHS08","GHS09"
];
const PHYSICAL_CONTROLS = [
  "通风","防泄漏托盘","防火","防爆","温度监控",
  "视频监控","入侵报警","双人双锁"
];
const HAZARDOUS_STATUS_LABELS = {
  not_assessed:"未评估", listed:"危险化学品",
  not_listed:"未在目录检出", pending_review:"待法规核验"
};
const inventoryState = {
  items:[], summary:null, movements:[], movementTotal:0,
  movementOffset:0, movementLimit:50, session:null, selected:null,
  locations:[], approvals:[]
};

function inventoryEventId(prefix) {
  const unique = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${unique}`;
}

function stableInventoryEventId(form, prefix) {
  if (!form.dataset.clientEventId) {
    form.dataset.clientEventId = inventoryEventId(prefix);
  }
  return form.dataset.clientEventId;
}

function lockInventoryForm(event) {
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

async function inventoryApi(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const csrfHeaders = !["GET","HEAD","OPTIONS"].includes(method)
    && inventoryState.session?.csrf_token
    ? {"X-CSRF-Token":inventoryState.session.csrf_token}
    : {};
  const response = await fetch(url, {
    ...options,
    headers:{
      Accept:"application/json",
      ...(options.body ? {"Content-Type":"application/json"} : {}),
      ...csrfHeaders,
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

function setInventoryStatus(message, error = false, target = "inventoryStatus") {
  const node = inventoryById(target);
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", error);
}

function textElement(tag, text, className = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function canManageInventory() {
  return ["super_admin","supervisor"].includes(inventoryState.session?.role);
}

function renderLocationOptions() {
  const list = inventoryById("inventoryLocationOptions");
  list.textContent = "";
  inventoryState.locations.forEach(location => {
    const option = document.createElement("option");
    option.value = location.location_code;
    option.label = `${location.display_name} · ${
      (location.allowed_storage_groups || [])
        .map(group => STORAGE_GROUP_LABELS[group] || group).join("、")
    }`;
    list.appendChild(option);
  });
}

function renderApprovals() {
  const panel = inventoryById("inventoryApprovalPanel");
  panel.hidden = !canManageInventory();
  if (panel.hidden) return;
  const host = inventoryById("inventoryApprovalRows");
  host.textContent = "";
  inventoryById("inventoryApprovalCount").textContent =
    `${inventoryState.approvals.length} 项`;
  if (!inventoryState.approvals.length) {
    host.appendChild(textElement("p", "暂无待确认操作。", "muted"));
    return;
  }
  inventoryState.approvals.forEach(approval => {
    const row = document.createElement("div");
    row.className = "movement";
    const request = approval.request_payload || {};
    const amount = request.quantity ?? request.actual_quantity ?? "—";
    row.append(
      textElement(
        "strong",
        `${ACTION_LABELS[approval.action] || approval.action} · ${
          approval.material_name
        } · ${amount} ${approval.unit || ""}`
      ),
      textElement(
        "div",
        `${new Date(approval.requested_at_ms).toLocaleString("zh-CN")} · 申请人 ${
          approval.requested_by
        }${request.note ? ` · ${request.note}` : ""}`,
        "muted"
      )
    );
    const controls = document.createElement("div");
    controls.className = "inline";
    controls.style.marginTop = "8px";
    [["approve","确认并执行","primary"],["reject","驳回","danger"]]
      .forEach(([decision,label,tone]) => {
        const button = textElement("button", label, `btn small ${tone}`);
        button.type = "button";
        button.addEventListener("click", async () => {
          try {
            await inventoryApi(
              `/api/inventory/movement-approvals/${approval.id}/decision`,
              {
                method:"POST",
                body:JSON.stringify({decision})
              }
            );
            await loadInventory();
          } catch (error) {
            showInventoryError(error);
          }
        });
        controls.appendChild(button);
      });
    row.appendChild(controls);
    host.appendChild(row);
  });
}

function renderSummary() {
  const summary = inventoryState.summary || {};
  const cards = [
    ["物品总数","total_items",""],
    ["危险化学品","hazardous","danger"],
    ["合规资料待补","compliance_incomplete","danger"],
    ["待双人确认","pending_dual_approvals","warn"],
    ["低库存","low_stock","warn"],
    ["库存超量","over_stock","warn"],
    ["30 天内临期","expiring","warn"],
    ["已过期","expired","danger"],
    ["待处置","to_dispose","danger"],
    ["已用完","used_up",""]
  ];
  const grid = inventoryById("inventorySummaryGrid");
  grid.textContent = "";
  cards.forEach(([label,key,tone]) => {
    const card = document.createElement("article");
    card.className = `summary-card ${tone}`;
    card.append(
      textElement("span", label),
      textElement("strong", String(summary[key] || 0))
    );
    grid.appendChild(card);
  });
}

function renderAlertDetails() {
  const summary = inventoryState.summary || {};
  const host = inventoryById("inventoryAlertDetails");
  host.textContent = "";
  const groups = [
    ["低库存",summary.low_stock_items || [],item =>
      `${item.material_name}：${item.quantity_remaining} ${item.unit}`],
    ["临期/过期",[
      ...(summary.expiring_items || []),
      ...(summary.expired_items || [])
    ],item => `${item.material_name}：${item.expires_on}`],
    ["待处置",summary.to_dispose_items || [],item =>
      `${item.material_name}：${item.quantity_remaining} ${item.unit}`],
    ["合规资料待补",summary.compliance_incomplete_items || [],item =>
      `${item.material_name}：${HAZARDOUS_STATUS_LABELS[
        item.hazardous_status
      ] || item.hazardous_status}`]
  ];
  groups.forEach(([label,items,format]) => {
    const group = document.createElement("article");
    group.className = "alert-group";
    group.appendChild(textElement("h3", `${label} · ${items.length}`));
    if (!items.length) {
      group.appendChild(textElement("p", "暂无", "muted"));
    } else {
      const list = document.createElement("ul");
      items.slice(0,8).forEach(item => {
        const entry = document.createElement("li");
        const button = textElement("button", format(item), "btn small");
        button.type = "button";
        button.addEventListener("click", () =>
          openInventoryDetail(item.id).catch(showInventoryError)
        );
        entry.appendChild(button);
        list.appendChild(entry);
      });
      group.appendChild(list);
    }
    host.appendChild(group);
  });
  const hazards = Object.entries(summary.hazard_counts || {})
    .sort((left,right) => right[1] - left[1])
    .map(([hazard,count]) => `${hazard} ${count}`)
    .join(" · ");
  const adjustText = summary.days_since_last_adjust == null
    ? "尚未记录库存盘点"
    : `距上次盘点 ${summary.days_since_last_adjust} 天`;
  inventoryById("inventoryAdjustStatus").textContent =
    `${adjustText}${hazards ? ` · 危险性：${hazards}` : ""}`;
}

function itemStatus(item) {
  if (
    item.category === "chemical" && item.expires_on &&
    item.expires_on < new Date().toISOString().slice(0,10)
  ) return "expired";
  return item.status;
}

function renderItems() {
  const body = inventoryById("inventoryRows");
  body.textContent = "";
  inventoryById("inventoryCount").textContent =
    `共 ${inventoryState.items.length} 条`;
  const printLink = inventoryById("printInventoryLabels");
  const printIds = inventoryState.items
    .filter(item => item.category === "chemical")
    .map(item => item.id)
    .join(",");
  printLink.href = printIds
    ? `/api/material-containers/labels?ids=${encodeURIComponent(printIds)}`
    : "#";
  printLink.hidden = !printIds;
  const locationPrintLink = inventoryById("printStorageLocationLabels");
  const locationIds = inventoryState.locations
    .map(location => location.id)
    .join(",");
  locationPrintLink.href = locationIds
    ? `/api/storage-locations/labels?ids=${encodeURIComponent(locationIds)}`
    : "#";
  locationPrintLink.hidden = !locationIds;
  if (!inventoryState.items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.className = "muted";
    cell.textContent = "没有符合条件的物品。";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  inventoryState.items.forEach(item => {
    const row = document.createElement("tr");
    row.dataset.itemId = item.id;
    const status = itemStatus(item);
    const values = [
      item.container_code,
      item.material_name,
      CATEGORY_LABELS[item.category] || item.category,
      item.quantity_remaining == null
        ? "待盘点"
        : `${item.quantity_remaining} ${item.unit || ""}`.trim(),
      item.location || "—",
      `${item.supplier_lot || "—"} / ${item.expires_on || "—"}`
    ];
    values.forEach(value => row.appendChild(textElement("td", value)));
    const statusCell = document.createElement("td");
    const statusBadge = textElement(
      "span", STATUS_LABELS[status] || status, `badge ${status}`
    );
    statusCell.appendChild(statusBadge);
    if (item.is_controlled) {
      statusCell.append(" ", textElement("span", "管制", "badge controlled"));
    }
    if (item.hazardous_status === "listed") {
      statusCell.append(" ", textElement("span", "危化品", "badge controlled"));
    } else if (item.hazardous_status === "pending_review") {
      statusCell.append(" ", textElement("span", "待核验", "badge"));
    }
    const actionCell = document.createElement("td");
    const detail = textElement("button", "详情", "btn small");
    detail.type = "button";
    detail.addEventListener("click", event => {
      event.stopPropagation();
      openInventoryDetail(item.id).catch(showInventoryError);
    });
    const label = textElement("a", "标签", "btn small");
    label.href = `/api/material-containers/${item.id}/label`;
    label.target = "_blank";
    label.rel = "noopener";
    label.addEventListener("click", event => event.stopPropagation());
    actionCell.append(detail, " ", label);
    row.append(statusCell, actionCell);
    row.addEventListener("click", () =>
      openInventoryDetail(item.id).catch(showInventoryError)
    );
    body.appendChild(row);
  });
}

function renderAudit() {
  const host = inventoryById("inventoryAuditRows");
  host.textContent = "";
  inventoryById("inventoryAuditCount").textContent =
    `共 ${inventoryState.movementTotal} 条流水`;
  const totalPages = Math.max(
    1, Math.ceil(inventoryState.movementTotal / inventoryState.movementLimit)
  );
  const currentPage = Math.floor(
    inventoryState.movementOffset / inventoryState.movementLimit
  ) + 1;
  inventoryById("inventoryAuditPage").textContent =
    `${currentPage} / ${totalPages}`;
  inventoryById("inventoryAuditPrevious").disabled = currentPage <= 1;
  inventoryById("inventoryAuditNext").disabled = currentPage >= totalPages;
  if (!inventoryState.movements.length) {
    host.appendChild(textElement("p", "暂无库存流水。", "muted"));
    return;
  }
  inventoryState.movements.forEach(movement => {
    const row = document.createElement("div");
    row.className = "movement";
    const amount = movement.delta > 0
      ? `+${movement.delta}` : String(movement.delta);
    row.append(
      textElement(
        "strong",
        `${ACTION_LABELS[movement.action] || movement.action} · ${
          movement.material_name
        } · ${amount} ${movement.unit || ""}`
      ),
      textElement(
        "div",
        `${new Date(movement.effective_at_ms).toLocaleString("zh-CN")} · ${
          movement.actor
        }${movement.batch_id ? ` · 实验 ${movement.batch_id}` : ""}${
          movement.note ? ` · ${movement.note}` : ""
        }`,
        "muted"
      )
    );
    host.appendChild(row);
  });
}

function auditQuery() {
  const query = new URLSearchParams();
  const values = {
    action:inventoryById("inventoryAuditAction").value,
    from:inventoryById("inventoryAuditFrom").value,
    to:inventoryById("inventoryAuditTo").value,
    limit:String(inventoryState.movementLimit),
    offset:String(inventoryState.movementOffset)
  };
  Object.entries(values).forEach(([key,value]) => {
    if (value) query.set(key,value);
  });
  const exportQuery = new URLSearchParams(query);
  exportQuery.delete("limit");
  exportQuery.delete("offset");
  inventoryById("exportInventoryAudit").href =
    `/api/inventory/movements.csv?${exportQuery.toString()}`;
  return query.toString();
}

function filtersQuery() {
  const values = {
    search:inventoryById("inventorySearch").value.trim(),
    category:inventoryById("inventoryCategory").value,
    status:inventoryById("inventoryItemStatus").value,
    location:inventoryById("inventoryLocation").value.trim(),
    controlled:inventoryById("inventoryControlled").value
  };
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key,value]) => {
    if (value) query.set(key,value);
  });
  return query.toString();
}

async function loadInventory() {
  setInventoryStatus("正在读取库存…");
  const query = filtersQuery();
  const auditParams = auditQuery();
  const [summary,items,audit,session,locations] = await Promise.all([
    inventoryApi("/api/inventory/summary"),
    inventoryApi(`/api/inventory/items${query ? `?${query}` : ""}`),
    inventoryApi(`/api/inventory/movements?${auditParams}`),
    inventoryApi("/api/session"),
    inventoryApi("/api/storage-locations")
  ]);
  inventoryState.summary = summary;
  inventoryState.items = items;
  inventoryState.movements = audit.movements;
  inventoryState.movementTotal = audit.total;
  inventoryState.session = session;
  inventoryState.locations = locations;
  inventoryState.approvals = canManageInventory()
    ? (await inventoryApi("/api/inventory/movement-approvals")).approvals
    : [];
  inventoryById("openItemForm").hidden = !canManageInventory();
  inventoryById("openLocationForm").hidden = !canManageInventory();
  renderLocationOptions();
  renderApprovals();
  renderSummary();
  renderAlertDetails();
  renderItems();
  renderAudit();
  setInventoryStatus("库存已更新");
}

async function loadInventoryAudit() {
  const audit = await inventoryApi(
    `/api/inventory/movements?${auditQuery()}`
  );
  inventoryState.movements = audit.movements;
  inventoryState.movementTotal = audit.total;
  renderAudit();
}

function showInventoryError(error) {
  setInventoryStatus(error.message || "操作失败", true);
}

function renderHazardOptions(selected = []) {
  const host = inventoryById("itemHazards");
  host.textContent = "";
  HAZARDS.forEach(hazard => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = hazard;
    input.checked = selected.includes(hazard);
    label.append(input, document.createTextNode(hazard));
    host.appendChild(label);
  });
}

function renderControlCategoryOptions(selected = []) {
  const host = inventoryById("itemControlledCategories");
  host.textContent = "";
  CONTROL_CATEGORIES.forEach(category => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = category;
    input.checked = selected.includes(category);
    label.append(input, document.createTextNode(category));
    host.appendChild(label);
  });
}

function renderChoiceCheckboxes(hostId, values, selected = []) {
  const host = inventoryById(hostId);
  host.textContent = "";
  values.forEach(value => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = selected.includes(value);
    label.append(input, document.createTextNode(
      STORAGE_GROUP_LABELS[value] || value
    ));
    host.appendChild(label);
  });
}

function toggleChemicalFields() {
  inventoryById("chemicalFields").hidden =
    inventoryById("itemCategory").value !== "chemical";
}

function resetItemForm(item = null) {
  delete inventoryById("inventoryItemForm").dataset.clientEventId;
  const form = inventoryById("inventoryItemForm");
  form.reset();
  inventoryById("itemId").value = item?.id || "";
  inventoryById("itemFormTitle").textContent = item ? "编辑物品" : "新增物品";
  inventoryById("itemQuantity").disabled = Boolean(item);
  inventoryById("itemQuantity").closest(".field").hidden = Boolean(item);
  inventoryById("itemCategory").value = item?.category || "chemical";
  inventoryById("itemName").value = item?.material_name || "";
  inventoryById("itemQuantity").value = item?.quantity_remaining ?? 0;
  inventoryById("itemUnit").value = item?.unit || "";
  inventoryById("itemLocation").value = item?.location || "";
  inventoryById("itemOwner").value = item?.owner || "";
  inventoryById("itemMin").value = item?.min_threshold ?? "";
  inventoryById("itemMax").value = item?.max_threshold ?? "";
  inventoryById("itemExternalBarcode").value = item?.external_barcode || "";
  inventoryById("itemLot").value = item?.supplier_lot || "";
  inventoryById("itemSourceOrganization").value =
    item?.source_organization || "";
  inventoryById("itemHandoverNo").value =
    item?.handover_document_no || "";
  inventoryById("itemHandoverRef").value =
    item?.handover_document_ref || "";
  inventoryById("itemReceivedDate").value = item?.received_at_ms
    ? new Date(item.received_at_ms).toISOString().slice(0,10) : "";
  inventoryById("itemReceivedBy").value = item?.received_by || "";
  inventoryById("itemAcceptedBy").value = item?.accepted_by || "";
  inventoryById("itemFilingNo").value =
    item?.regulatory_filing_no || "";
  inventoryById("itemFilingRef").value =
    item?.regulatory_filing_ref || "";
  inventoryById("itemControlled").checked = Boolean(item?.is_controlled);
  inventoryById("itemHazardousStatus").value =
    item?.hazardous_status || "not_assessed";
  inventoryById("itemStorageGroup").value =
    item?.storage_group || "unassessed";
  inventoryById("itemCas").value = item?.cas_no || "";
  inventoryById("itemSpec").value = item?.spec || "";
  inventoryById("itemSds").value = item?.sds_url || "";
  inventoryById("itemSdsRevision").value = item?.sds_revision || "";
  inventoryById("itemSdsVerified").checked =
    Boolean(item?.sds_verified_at_ms);
  inventoryById("itemCatalogSource").value = item?.catalog_source || "";
  inventoryById("itemCatalogVersion").value = item?.catalog_version || "";
  inventoryById("itemCatalogEntry").value = item?.catalog_entry_no || "";
  inventoryById("itemRegulatoryReviewed").checked =
    Boolean(item?.regulatory_reviewed_at_ms);
  inventoryById("itemDualControl").checked =
    Boolean(item?.dual_control_required);
  inventoryById("itemDualReason").value = item?.dual_control_reason || "";
  inventoryById("itemExpiry").value = item?.expires_on || "";
  inventoryById("itemOpened").value = item?.opened_on || "";
  inventoryById("itemPreparedBy").value = item?.prepared_by || "";
  inventoryById("itemPreparedDate").value = item?.prepared_date || "";
  inventoryById("itemNote").value = item?.note || "";
  renderHazardOptions(item?.hazards || []);
  renderControlCategoryOptions(item?.controlled_categories || []);
  renderChoiceCheckboxes(
    "itemGhsPictograms", GHS_PICTOGRAMS, item?.ghs_pictograms || []
  );
  toggleChemicalFields();
  setInventoryStatus("", false, "itemFormStatus");
}

function itemFormPayload() {
  const optionalNumber = id => {
    const value = inventoryById(id).value;
    return value === "" ? null : Number(value);
  };
  return {
    name:inventoryById("itemName").value,
    category:inventoryById("itemCategory").value,
    quantity:Number(inventoryById("itemQuantity").value || 0),
    unit:inventoryById("itemUnit").value,
    location:inventoryById("itemLocation").value || null,
    owner:inventoryById("itemOwner").value || null,
    min_threshold:optionalNumber("itemMin"),
    max_threshold:optionalNumber("itemMax"),
    external_barcode:inventoryById("itemExternalBarcode").value || null,
    lot_no:inventoryById("itemLot").value || null,
    source_organization:
      inventoryById("itemSourceOrganization").value || null,
    handover_document_no:inventoryById("itemHandoverNo").value || null,
    handover_document_ref:inventoryById("itemHandoverRef").value || null,
    received_date:inventoryById("itemReceivedDate").value || null,
    received_by:inventoryById("itemReceivedBy").value || null,
    accepted_by:inventoryById("itemAcceptedBy").value || null,
    regulatory_filing_no:inventoryById("itemFilingNo").value || null,
    regulatory_filing_ref:inventoryById("itemFilingRef").value || null,
    is_controlled:inventoryById("itemControlled").checked,
    hazardous_status:inventoryById("itemHazardousStatus").value,
    storage_group:inventoryById("itemStorageGroup").value,
    controlled_categories:[
      ...inventoryById("itemControlledCategories")
        .querySelectorAll("input:checked")
    ].map(input => input.value),
    cas_no:inventoryById("itemCas").value || null,
    spec:inventoryById("itemSpec").value || null,
    sds_url:inventoryById("itemSds").value || null,
    sds_revision:inventoryById("itemSdsRevision").value || null,
    sds_verified:inventoryById("itemSdsVerified").checked,
    catalog_source:inventoryById("itemCatalogSource").value || null,
    catalog_version:inventoryById("itemCatalogVersion").value || null,
    catalog_entry_no:inventoryById("itemCatalogEntry").value || null,
    regulatory_review_confirmed:
      inventoryById("itemRegulatoryReviewed").checked,
    dual_control_required:inventoryById("itemDualControl").checked,
    dual_control_reason:inventoryById("itemDualReason").value || null,
    ghs_pictograms:[
      ...inventoryById("itemGhsPictograms")
        .querySelectorAll("input:checked")
    ].map(input => input.value),
    compliance_review:Boolean(inventoryById("itemId").value),
    expiry_date:inventoryById("itemExpiry").value || null,
    opened_date:inventoryById("itemOpened").value || null,
    prepared_by:inventoryById("itemPreparedBy").value || null,
    prepared_date:inventoryById("itemPreparedDate").value || null,
    hazards:[...inventoryById("itemHazards").querySelectorAll("input:checked")]
      .map(input => input.value),
    note:inventoryById("itemNote").value || null,
    client_event_id:inventoryEventId("inventory-create")
  };
}

async function submitItemForm(event) {
  event.preventDefault();
  const unlock = lockInventoryForm(event);
  if (!unlock) return;
  const id = inventoryById("itemId").value;
  const payload = itemFormPayload();
  payload.client_event_id = stableInventoryEventId(
    event.currentTarget, id ? `inventory-update-${id}` : "inventory-create"
  );
  try {
    const item = await inventoryApi(
      id ? `/api/inventory/items/${id}` : "/api/inventory/items",
      {method:id ? "PATCH" : "POST", body:JSON.stringify(payload)}
    );
    inventoryById("itemFormDialog").close();
    delete event.currentTarget.dataset.clientEventId;
    await loadInventory();
    await openInventoryDetail(item.id);
  } catch (error) {
    setInventoryStatus(error.message, true, "itemFormStatus");
  } finally {
    unlock();
  }
}

function appendDetailValue(host, label, value) {
  const card = document.createElement("div");
  card.className = "detail-value";
  card.append(textElement("span", label), textElement("strong", value || "—"));
  host.appendChild(card);
}

async function openInventoryDetail(itemId) {
  const detail = await inventoryApi(`/api/inventory/items/${itemId}`);
  const item = detail.item;
  inventoryState.selected = item;
  inventoryById("inventoryDetailTitle").textContent =
    `${item.material_name} · ${item.container_code}`;
  const body = inventoryById("inventoryDetailBody");
  body.textContent = "";
  if (item.is_controlled) {
    body.appendChild(textElement(
      "p", "管制类物品：每次领用自动记录登录账号。", "badge controlled"
    ));
  }
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  [
    ["类别",CATEGORY_LABELS[item.category] || item.category],
    ["当前库存",item.quantity_remaining == null
      ? "待盘点"
      : `${item.quantity_remaining} ${item.unit}`],
    ["状态",STATUS_LABELS[itemStatus(item)] || itemStatus(item)],
    ["位置",item.location],
    ["负责人",item.owner],
    ["供应商批号",item.supplier_lot],
    ["来源单位",item.source_organization],
    ["交付凭证",item.handover_document_no],
    ["接收/验收",[
      item.received_by,item.accepted_by
    ].filter(Boolean).join(" / ")],
    ["许可/备案",item.regulatory_filing_no],
    ["有效期",item.expires_on],
    ["CAS",item.cas_no],
    ["规格",item.spec],
    ["危险性",(item.hazards || []).join(" / ")],
    ["GHS 象形图",(item.ghs_pictograms || []).join(" / ")],
    ["储存组",STORAGE_GROUP_LABELS[item.storage_group]
      || item.storage_group],
    ["危化品判定",HAZARDOUS_STATUS_LABELS[item.hazardous_status]
      || item.hazardous_status],
    ["特殊管制",(item.controlled_categories || []).join(" / ")],
    ["目录判定",[
      item.catalog_source,item.catalog_version,item.catalog_entry_no
    ].filter(Boolean).join(" / ")],
    ["SDS 核验",item.sds_verified_at_ms
      ? `${item.sds_verified_by || "已核验"} · ${
        new Date(item.sds_verified_at_ms).toLocaleString("zh-CN")
      }`
      : "未核验"],
    ["双人控制",item.dual_control_required
      ? `是 · ${item.dual_control_reason || "按库位/类别要求"}`
      : "否"],
    ["登记人",item.created_by],
    ["开封日期",item.opened_on]
  ].forEach(([label,value]) => appendDetailValue(grid,label,String(value || "—")));
  body.appendChild(grid);
  if (item.sds_url) {
    const emergency = textElement(
      "a", "打开 SDS / 查看泄漏与急救信息", "btn danger"
    );
    emergency.href = item.sds_url;
    emergency.target = "_blank";
    emergency.rel = "noopener";
    emergency.style.display = "inline-flex";
    emergency.style.marginTop = "12px";
    body.appendChild(emergency);
  }
  const actions = document.createElement("div");
  actions.className = "toolbar";
  actions.style.marginTop = "14px";
  const actionOptions = [
    ["issued","领用","primary"],
    ["received","入库",""],
    ["adjusted","盘点",""],
    ["quarantined","隔离","danger"],
    ["disposed","处置","danger"]
  ];
  actionOptions.forEach(([action,label,tone]) => {
    if (
      ["received","adjusted","quarantined","disposed"].includes(action) &&
      !canManageInventory()
    ) return;
    const button = textElement("button", label, `btn ${tone}`);
    button.type = "button";
    button.disabled = action === "issued" && (
      item.status !== "available" || item.quantity_remaining == null
    );
    button.addEventListener("click", () => openOperation(item,action,label));
    actions.appendChild(button);
  });
  if (canManageInventory()) {
    const edit = textElement("button", "编辑资料", "btn");
    edit.type = "button";
    edit.addEventListener("click", () => {
      inventoryById("inventoryDetailDialog").close();
      resetItemForm(item);
      inventoryById("itemFormDialog").showModal();
    });
    actions.appendChild(edit);
  }
  const print = textElement("a", "打印标签", "btn");
  print.href = `/api/material-containers/${item.id}/label`;
  print.target = "_blank";
  print.rel = "noopener";
  actions.appendChild(print);
  body.appendChild(actions);
  body.appendChild(textElement("h3", "库存流水"));
  detail.movements.forEach(movement => {
    const row = document.createElement("div");
    row.className = "movement";
    row.append(
      textElement(
        "strong",
        `${ACTION_LABELS[movement.action] || movement.action} · ${
          movement.delta > 0 ? "+" : ""
        }${movement.delta} ${movement.unit || ""}`
      ),
      textElement(
        "div",
        `${new Date(movement.effective_at_ms).toLocaleString("zh-CN")} · ${
          movement.actor
        }${movement.batch_id ? ` · ${movement.batch_id}` : ""}${
          movement.note ? ` · ${movement.note}` : ""
        }`,
        "muted"
      )
    );
    body.appendChild(row);
  });
  inventoryById("inventoryDetailDialog").showModal();
}

function openOperation(item, action, label) {
  delete inventoryById("inventoryOperationForm").dataset.clientEventId;
  inventoryById("operationItemId").value = item.id;
  inventoryById("operationAction").value = action;
  inventoryById("inventoryOperationTitle").textContent =
    `${label} · ${item.material_name}`;
  const needsQuantity = ["received","issued","adjusted"].includes(action);
  inventoryById("operationQuantityField").hidden = !needsQuantity;
  inventoryById("operationQuantity").required = needsQuantity;
  inventoryById("operationQuantity").value =
    action === "adjusted" ? item.quantity_remaining : "";
  inventoryById("operationQuantityLabel").textContent =
    action === "adjusted" ? "实际盘点数量" : `数量（${item.unit}）`;
  inventoryById("operationDualNotice").hidden = !(
    item.dual_control_required &&
    ["received","issued","adjusted","disposed"].includes(action)
  );
  inventoryById("operationNote").value = "";
  setInventoryStatus("", false, "operationStatus");
  inventoryById("inventoryOperationDialog").showModal();
}

async function submitOperation(event) {
  event.preventDefault();
  const unlock = lockInventoryForm(event);
  if (!unlock) return;
  const itemId = inventoryById("operationItemId").value;
  const action = inventoryById("operationAction").value;
  const quantity = Number(inventoryById("operationQuantity").value);
  const payload = {
    action,
    note:inventoryById("operationNote").value || null,
    client_event_id:stableInventoryEventId(
      event.currentTarget, `inventory-${action}-${itemId}`
    )
  };
  if (["received","issued"].includes(action)) payload.quantity = quantity;
  if (action === "adjusted") payload.actual_quantity = quantity;
  try {
    const requiresApproval = !inventoryById("operationDualNotice").hidden;
    await inventoryApi(
      requiresApproval
        ? `/api/inventory/items/${itemId}/movement-approvals`
        : `/api/inventory/items/${itemId}/movements`,
      {
      method:"POST", body:JSON.stringify(payload)
      }
    );
    inventoryById("inventoryOperationDialog").close();
    delete event.currentTarget.dataset.clientEventId;
    inventoryById("inventoryDetailDialog").close();
    await loadInventory();
    if (requiresApproval) {
      setInventoryStatus("已提交，等待第二个授权账号确认");
    } else {
      await openInventoryDetail(itemId);
    }
  } catch (error) {
    setInventoryStatus(error.message, true, "operationStatus");
  } finally {
    unlock();
  }
}

async function lookupCas() {
  const cas = inventoryById("itemCas").value.trim();
  setInventoryStatus(
    "正在查询 PubChem 和国家危化品目录…",
    false,
    "itemFormStatus"
  );
  try {
    const [pubchemResult,regulatoryResult] = await Promise.allSettled([
      inventoryApi(`/api/inventory/pubchem?cas=${encodeURIComponent(cas)}`),
      inventoryApi(
        `/api/inventory/regulatory-lookup?cas=${encodeURIComponent(cas)}`
      )
    ]);
    if (
      pubchemResult.status === "rejected" &&
      regulatoryResult.status === "rejected"
    ) {
      throw regulatoryResult.reason;
    }
    const result = pubchemResult.status === "fulfilled"
      ? pubchemResult.value : {};
    const regulatory = regulatoryResult.status === "fulfilled"
      ? regulatoryResult.value : null;
    if (!inventoryById("itemName").value) {
      inventoryById("itemName").value = result.name || "";
    }
    if (!inventoryById("itemSpec").value) {
      inventoryById("itemSpec").value = result.spec || "";
    }
    if (!inventoryById("itemSds").value) {
      inventoryById("itemSds").value = result.sds_url || "";
    }
    renderHazardOptions([
      ...new Set([
        ...[...inventoryById("itemHazards").querySelectorAll("input:checked")]
          .map(input => input.value),
        ...(result.hazards || [])
      ])
    ]);
    if (regulatory) {
      inventoryById("itemHazardousStatus").value =
        regulatory.hazardous_status;
      inventoryById("itemCatalogSource").value =
        regulatory.catalog_source || "";
      inventoryById("itemCatalogVersion").value =
        regulatory.catalog_version || "";
      inventoryById("itemCatalogEntry").value =
        (regulatory.catalog_entries || [])
          .map(entry => entry.entry_no).join(",");
      renderChoiceCheckboxes(
        "itemGhsPictograms",
        GHS_PICTOGRAMS,
        [
          ...new Set([
            ...[...inventoryById("itemGhsPictograms")
              .querySelectorAll("input:checked")]
              .map(input => input.value),
            ...(regulatory.suggested_ghs_pictograms || [])
          ])
        ]
      );
      const storageSuggestions = regulatory.suggested_storage_groups || [];
      if (
        inventoryById("itemStorageGroup").value === "unassessed" &&
        storageSuggestions.length === 1
      ) {
        inventoryById("itemStorageGroup").value = storageSuggestions[0];
      }
      inventoryById("itemRegulatoryReviewed").checked = false;
    }
    setInventoryStatus(
      regulatory?.warning || "已读取 PubChem；国家目录暂时无法访问",
      Boolean(regulatory && !regulatory.matched),
      "itemFormStatus"
    );
  } catch (error) {
    setInventoryStatus(error.message, true, "itemFormStatus");
  }
}

async function acceptInventoryScan(code) {
  const result = await inventoryApi(
    `/api/scan/resolve?code=${encodeURIComponent(String(code || "").trim())}`
  );
  if (result.kind !== "material_container") {
    throw new Error("该二维码不是库存物品标签");
  }
  await openInventoryDetail(result.material.id);
}

function resetLocationForm(location = null) {
  inventoryById("locationForm").reset();
  inventoryById("locationId").value = location?.id || "";
  inventoryById("locationFormTitle").textContent =
    location ? "编辑合规库位" : "配置合规库位";
  const existing = inventoryById("locationExisting");
  existing.textContent = "";
  const createOption = document.createElement("option");
  createOption.value = "";
  createOption.textContent = "新建库位";
  existing.appendChild(createOption);
  inventoryState.locations.forEach(item => {
    const option = document.createElement("option");
    option.value = String(item.id);
    option.textContent = `${item.display_name}（${item.location_code}）`;
    existing.appendChild(option);
  });
  existing.value = location ? String(location.id) : "";
  inventoryById("locationCode").value = location?.location_code || "";
  inventoryById("locationCode").disabled = Boolean(location);
  inventoryById("locationName").value = location?.display_name || "";
  inventoryById("locationType").value =
    location?.location_type || "chemical_cabinet";
  inventoryById("locationCondition").value =
    location?.storage_condition || "";
  inventoryById("locationDualControl").checked =
    Boolean(location?.requires_dual_control);
  inventoryById("locationComplianceNote").value =
    location?.compliance_note || "";
  renderChoiceCheckboxes(
    "locationAllowedGroups",
    Object.keys(STORAGE_GROUP_LABELS).filter(value => value !== "unassessed"),
    location?.allowed_storage_groups || []
  );
  renderChoiceCheckboxes(
    "locationPhysicalControls",
    PHYSICAL_CONTROLS,
    location?.physical_controls || []
  );
  setInventoryStatus("", false, "locationFormStatus");
}

async function submitLocationForm(event) {
  event.preventDefault();
  const checked = hostId => [
    ...inventoryById(hostId).querySelectorAll("input:checked")
  ].map(input => input.value);
  const locationId = inventoryById("locationId").value;
  try {
    await inventoryApi(
      locationId
        ? `/api/storage-locations/${locationId}`
        : "/api/storage-locations",
      {
      method:locationId ? "PATCH" : "POST",
      body:JSON.stringify({
        location_code:inventoryById("locationCode").value,
        display_name:inventoryById("locationName").value,
        location_type:inventoryById("locationType").value,
        storage_condition:inventoryById("locationCondition").value || null,
        allowed_storage_groups:checked("locationAllowedGroups"),
        physical_controls:checked("locationPhysicalControls"),
        requires_dual_control:
          inventoryById("locationDualControl").checked,
        compliance_note:
          inventoryById("locationComplianceNote").value || null
      })
    });
    inventoryById("locationFormDialog").close();
    await loadInventory();
    setInventoryStatus("合规库位已保存");
  } catch (error) {
    setInventoryStatus(error.message, true, "locationFormStatus");
  }
}

function wireInventoryEvents() {
  inventoryById("inventoryFilters").addEventListener("input", () => {
    clearTimeout(wireInventoryEvents.filterTimer);
    wireInventoryEvents.filterTimer = setTimeout(
      () => loadInventory().catch(showInventoryError), 180
    );
  });
  inventoryById("refreshInventory").addEventListener(
    "click", () => loadInventory().catch(showInventoryError)
  );
  inventoryById("inventoryAuditFilters").addEventListener(
    "submit", event => {
      event.preventDefault();
      inventoryState.movementOffset = 0;
      loadInventoryAudit().catch(showInventoryError);
    }
  );
  inventoryById("refreshInventoryAudit").addEventListener(
    "click", () => {
      inventoryState.movementOffset = 0;
      loadInventoryAudit().catch(showInventoryError);
    }
  );
  ["inventoryAuditAction","inventoryAuditFrom","inventoryAuditTo"].forEach(id => {
    inventoryById(id).addEventListener("change", () => {
      inventoryState.movementOffset = 0;
      auditQuery();
    });
  });
  inventoryById("inventoryAuditPrevious").addEventListener("click", () => {
    inventoryState.movementOffset = Math.max(
      0, inventoryState.movementOffset - inventoryState.movementLimit
    );
    loadInventoryAudit().catch(showInventoryError);
  });
  inventoryById("inventoryAuditNext").addEventListener("click", () => {
    if (
      inventoryState.movementOffset + inventoryState.movementLimit
      >= inventoryState.movementTotal
    ) return;
    inventoryState.movementOffset += inventoryState.movementLimit;
    loadInventoryAudit().catch(showInventoryError);
  });
  inventoryById("openItemForm").addEventListener("click", () => {
    resetItemForm();
    inventoryById("itemFormDialog").showModal();
  });
  inventoryById("openLocationForm").addEventListener("click", () => {
    resetLocationForm();
    inventoryById("locationFormDialog").showModal();
  });
  inventoryById("locationExisting").addEventListener("change", event => {
    const selected = inventoryState.locations.find(
      location => String(location.id) === event.currentTarget.value
    );
    resetLocationForm(selected || null);
  });
  inventoryById("locationForm").addEventListener(
    "submit", submitLocationForm
  );
  inventoryById("openInventoryScanner").addEventListener("click", () => {
    PuricoreScanner.open({onResult:acceptInventoryScan}).catch(showInventoryError);
  });
  inventoryById("inventoryItemForm").addEventListener("submit", submitItemForm);
  inventoryById("inventoryOperationForm").addEventListener(
    "submit", submitOperation
  );
  inventoryById("itemCategory").addEventListener(
    "change", toggleChemicalFields
  );
  inventoryById("lookupCas").addEventListener("click", lookupCas);
  [
    "itemCas",
    "itemHazardousStatus",
    "itemCatalogSource",
    "itemCatalogVersion",
    "itemCatalogEntry"
  ].forEach(id => {
    inventoryById(id).addEventListener("input", () => {
      inventoryById("itemRegulatoryReviewed").checked = false;
    });
  });
  ["itemSds","itemSdsRevision"].forEach(id => {
    inventoryById(id).addEventListener("input", () => {
      inventoryById("itemSdsVerified").checked = false;
    });
  });
  document.querySelectorAll("[data-close]").forEach(button => {
    button.addEventListener("click", () =>
      inventoryById(button.dataset.close).close()
    );
  });
  inventoryById("cameraScanClose").addEventListener("click", async () => {
    await PuricoreScanner.stop();
    inventoryById("cameraScanDialog").close();
  });
  inventoryById("cameraScanManualSubmit").addEventListener(
    "click", () => PuricoreScanner.submitManual()
  );
}

wireInventoryEvents();
renderHazardOptions();
renderControlCategoryOptions();
renderChoiceCheckboxes("itemGhsPictograms", GHS_PICTOGRAMS);
renderChoiceCheckboxes(
  "locationAllowedGroups",
  Object.keys(STORAGE_GROUP_LABELS).filter(value => value !== "unassessed")
);
renderChoiceCheckboxes("locationPhysicalControls", PHYSICAL_CONTROLS);
loadInventory().then(async () => {
  const scanned = new URLSearchParams(location.search).get("scan");
  if (scanned) await acceptInventoryScan(scanned);
}).catch(showInventoryError);
