(function () {
  const STATUS_LABELS = {
    completed: "正常完成",
    alarm_abort: "报警终止",
    manual_abort: "手动终止",
    interrupted_restart: "系统重启中断",
    interrupted_reconnect: "通讯重连中断",
    interrupted_shutdown: "系统关闭中断",
    comms_interrupted: "通讯中断",
  };

  function statusLabel(run) {
    return run.status_label || STATUS_LABELS[run.end_status] || (run.end_status ? run.end_status : "运行中");
  }

  function statusClass(run) {
    if (!run.end_status) return "s-running";
    if (run.end_status === "completed") return "s-running";
    if (run.end_status === "alarm_abort") return "s-alarm";
    return "s-stopped";
  }

  function deviceLabel(run) {
    return run.device_label || run.device_alias || run.device_name || `设备 ${run.device_id}`;
  }

  function formatMetric(metric) {
    if (!metric || metric.value == null || metric.value === "") return `${metric && metric.label ? metric.label + "：" : ""}—`;
    const number = Number(metric.value);
    const value = Number.isFinite(number) ? Number(number.toFixed(2)) : metric.value;
    return `${metric.label || "数据"}：${value}${metric.unit ? ` ${metric.unit}` : ""}`;
  }

  window.PuricoreRunPresentation = { statusLabel, statusClass, deviceLabel, formatMetric };
}());
