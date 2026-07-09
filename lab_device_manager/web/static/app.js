const $ = (id) => document.getElementById(id);
const fmtDur = (ms) => (ms == null) ? "-" : (ms >= 60000 ? (ms/60000).toFixed(1)+" 分" : (ms/1000).toFixed(0)+" 秒");
const fmtTs = (ms) => (ms == null) ? "-" : new Date(ms).toLocaleString();

async function pollStatus() {
  let d;
  try { d = await (await fetch("/api/status")).json(); } catch (e) { return; }
  const box = $("devices");
  box.textContent = "";
  for (const dev of (d.devices || [])) {
    const L = dev.latest || {};
    const M = L.metrics || {};
    
    if (dev.type === "whd46") {
      const card = document.createElement("div");
      card.className = "card sensor-card";
      card.dataset.deviceId = dev.id;
      card.onclick = () => { location.href = "/sensor/" + dev.id; };
      
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = dev.alias || dev.name;
      
      const st = document.createElement("div");
      st.className = "v s-" + (L.state || "offline");
      st.textContent = L.state || "offline";
      
      const channels = M.channels || [];
      const ch1 = channels[0] || {};
      const ch2 = channels[1] || {};
      const ch3 = channels[2] || {};
      
      const temp = document.createElement("div");
      temp.className = "k";
      temp.textContent = "CH1 " + (ch1.temp != null ? ch1.temp.toFixed(1) + "℃" : "-") + 
        " / CH2 " + (ch2.temp != null ? ch2.temp.toFixed(1) + "℃" : "-") +
        " / CH3 " + (ch3.temp != null ? ch3.temp.toFixed(1) + "℃" : "-");
      
      const humid = document.createElement("div");
      humid.className = "k";
      humid.textContent = "CH1 " + (ch1.humid != null ? ch1.humid.toFixed(1) + "%RH" : "-") + 
        " / CH2 " + (ch2.humid != null ? ch2.humid.toFixed(1) + "%RH" : "-") +
        " / CH3 " + (ch3.humid != null ? ch3.humid.toFixed(1) + "%RH" : "-");
      
      const rtime = document.createElement("div");
      rtime.className = "k";
      rtime.textContent = "更新 " + fmtTs(L.ts_ms);
      
      card.appendChild(name);
      card.appendChild(st);
      card.appendChild(temp);
      card.appendChild(humid);
      card.appendChild(rtime);
      box.appendChild(card);
    } else {
      const card = document.createElement("div");
      card.className = "card";
      card.dataset.deviceId = dev.id;
      card.onclick = () => { location.href = "/device/" + dev.id; };
      
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = dev.alias || dev.name;
      
      const st = document.createElement("div");
      st.className = "v s-" + (L.state || "offline");
      st.textContent = L.state || "offline";
      
      const flow = document.createElement("div");
      flow.className = "v";
      flow.textContent = (L.flow_rpm == null ? "-" : (+L.flow_rpm).toFixed(2)) + " rpm";
      
      const running = (L.state === "running" || L.state === "paused");
      const vol = document.createElement("div");
      vol.className = "v";
      if (running) {
        let cur = "-", u = M.target_unit || "";
        if (M.target_volume != null && L.remaining_volume != null) {
          cur = (M.target_volume - L.remaining_volume).toFixed(3);
        } else if (L.consumed_volume != null) {
          cur = L.consumed_volume;
          u = L.consumed_unit || u;
        }
        const tgt = (M.target_volume == null ? "-" : M.target_volume);
        vol.textContent = "本次 " + cur + " / " + tgt + " " + u;
      } else {
        vol.textContent = "累计 " + (L.acc_volume == null ? "-" : L.acc_volume) + " " + (L.acc_unit || "");
      }
      
      const prog = document.createElement("div");
      prog.className = "k";
      prog.textContent = "进度 " + (L.progress_pct == null ? "-" : (+L.progress_pct).toFixed(1) + "%");
      
      const temp = document.createElement("div");
      temp.className = "k";
      temp.textContent = "温度 " + (L.temp_c == null ? "-" : L.temp_c) + " ℃";
      
      const cfg = document.createElement("div");
      cfg.className = "cfg";
      const cfgParts = [];
      if (L.work_mode) cfgParts.push(L.work_mode);
      if (M.syringe_name != null) cfgParts.push("注射器 " + M.syringe_name);
      if (M.step_length_ul_per_step != null) cfgParts.push(M.step_length_ul_per_step.toFixed(3) + " μL/步");
      cfg.textContent = cfgParts.join(" · ");
      
      const rtime = document.createElement("div");
      rtime.className = "k";
      rtime.textContent = "读数时刻 " + fmtTs(L.ts_ms);
      
      card.appendChild(name);
      card.appendChild(st);
      card.appendChild(flow);
      card.appendChild(vol);
      card.appendChild(prog);
      card.appendChild(temp);
      card.appendChild(cfg);
      card.appendChild(rtime);
      box.appendChild(card);
    }
  }
}

async function loadRuns() {
  const params = new URLSearchParams();
  params.set("limit", "30");
  const project = $("fProject").value.trim();
  const operator = $("fOperator").value.trim();
  const status = $("fStatus").value;
  if (project) params.set("project_tag", project);
  if (operator) params.set("operator", operator);
  if (status) params.set("end_status", status);
  let runs = [];
  try { runs = await (await fetch("/api/runs?" + params.toString())).json(); } catch (e) { return; }
  const tb = $("runs").querySelector("tbody");
  tb.textContent = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.onclick = () => { location.href = "/run/" + r.id; };
    const cell = (txt, cls) => { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = (txt == null || txt === "") ? "-" : txt; return td; };
    tr.appendChild(cell(r.started_display));
    tr.appendChild(cell(fmtTs(r.ended_ms)));
    tr.appendChild(cell(r.device_id));
    tr.appendChild(cell(fmtDur(r.duration_ms)));
    const statusCls = "s-" + (r.end_status === "alarm_abort" ? "alarm" : (r.end_status === "completed" ? "running" : "stopped"));
    tr.appendChild(cell(r.end_status, statusCls));
    tr.appendChild(cell((r.result_acc_volume == null ? "-" : r.result_acc_volume) + " " + (r.result_acc_unit || "")));
    tr.appendChild(cell(r.operator));
    tr.appendChild(cell(r.project_tag));
    const act = document.createElement("td");
    if (!r.tagged) {
      const btn = document.createElement("button");
      btn.textContent = "补录";
      btn.onclick = (ev) => { ev.stopPropagation(); tagRun(r.id); };
      act.appendChild(btn);
    } else {
      act.textContent = "✓";
    }
    tr.appendChild(act);
    tb.appendChild(tr);
  }
}

function exportExcel() {
  const params = new URLSearchParams();
  const project = $("fProject").value.trim();
  const operator = $("fOperator").value.trim();
  const status = $("fStatus").value;
  if (project) params.set("project_tag", project);
  if (operator) params.set("operator", operator);
  if (status) params.set("end_status", status);
  window.location = "/api/runs/export.xlsx?" + params.toString();
}

async function tagRun(runId) {
  const operator = prompt("操作人？");
  if (operator === null) return;
  const project = prompt("项目 / 实验 tag？");
  if (project === null) return;
  try {
    await fetch("/api/runs/" + runId + "/tag", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({operator: operator || "", project_tag: project || "", experiment_tag: "", remark: ""})
    });
    loadRuns();
  } catch (e) {
    alert("补录失败，请重试");
  }
}

pollStatus();
loadRuns();
setInterval(pollStatus, 1000);
setInterval(loadRuns, 3000);