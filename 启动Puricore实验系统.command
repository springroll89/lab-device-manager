#!/bin/zsh

set -u

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
PID_FILE="$SCRIPT_DIR/data/puricore-shortcut.pid"
LOG_FILE="$SCRIPT_DIR/data/puricore-shortcut.log"

fail() {
  print ""
  print "启动失败：$1"
  print ""
  print "按任意键关闭窗口。"
  read -k 1
  exit 1
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  fail "没有找到项目虚拟环境 .venv，请先按照 README 完成依赖安装。"
fi

mkdir -p "$SCRIPT_DIR/data"

CONFIG_VALUES=("${(@f)$("$PYTHON_BIN" -c \
  'from lab_device_manager.config import load_config; c=load_config(); print(c.web_port); print("https" if c.tls_certfile else "http"); print(c.public_base_url)' \
  2>>"$LOG_FILE")}")
if (( ${#CONFIG_VALUES[@]} < 2 )); then
  fail "无法读取 config.toml/config.local.toml，请查看日志：$LOG_FILE"
fi

PORT="${CONFIG_VALUES[1]}"
SCHEME="${CONFIG_VALUES[2]}"
PUBLIC_BASE_URL="${CONFIG_VALUES[3]:-}"
LOCAL_BASE_URL="$SCHEME://127.0.0.1:$PORT"
PC_URL="${PUBLIC_BASE_URL:-$LOCAL_BASE_URL}"

DEFAULT_INTERFACE="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
LAN_IP=""
if [[ -n "$DEFAULT_INTERFACE" ]]; then
  LAN_IP="$(ipconfig getifaddr "$DEFAULT_INTERFACE" 2>/dev/null || true)"
fi
if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
fi
MOBILE_URL="${PUBLIC_BASE_URL:-}"
if [[ -z "$MOBILE_URL" && -n "$LAN_IP" ]]; then
  MOBILE_URL="$SCHEME://$LAN_IP:$PORT"
fi

puricore_is_ready() {
  curl -kfsS --max-time 2 "$LOCAL_BASE_URL/login" 2>/dev/null \
    | grep -q "Puricore"
}

port_is_occupied() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

show_access_info() {
  print ""
  print "Puricore 实验系统已启动"
  print "PC 端：$PC_URL"
  if [[ -n "$MOBILE_URL" ]]; then
    print "平板/手机：$MOBILE_URL"
    print -rn -- "$MOBILE_URL" | pbcopy
    print "平板地址已复制到剪贴板。请保证平板与 Mac 连接同一局域网。"
  else
    print "未识别到局域网 IP；请检查 Mac 的 Wi-Fi/网线连接。"
  fi
  print "运行日志：$LOG_FILE"
}

if puricore_is_ready; then
  show_access_info
  open "$PC_URL"
  osascript -e 'display notification "系统已经运行，已打开 PC 页面" with title "Puricore 实验系统"' >/dev/null 2>&1
  exit 0
fi

if port_is_occupied; then
  fail "端口 $PORT 已被其他程序占用，未启动第二个服务。"
fi

if [[ -f "$PID_FILE" ]]; then
  rm -f "$PID_FILE"
fi

print "正在启动 Puricore 实验系统，请稍候……"
"$PYTHON_BIN" -m lab_device_manager \
  >>"$LOG_FILE" 2>&1 </dev/null &
APP_PID=$!
print -r -- "$APP_PID" > "$PID_FILE"

cleanup_pid_file() {
  if [[ -f "$PID_FILE" && "$(<"$PID_FILE")" == "$APP_PID" ]]; then
    rm -f "$PID_FILE"
  fi
}

stop_child() {
  trap - HUP INT TERM
  if kill -0 "$APP_PID" 2>/dev/null; then
    kill -TERM "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  cleanup_pid_file
  exit 0
}

trap stop_child HUP INT TERM

for attempt in {1..20}; do
  if puricore_is_ready; then
    show_access_info
    print "服务运行期间请保留此窗口（可以最小化）；也可以双击“停止Puricore实验系统”安全停止。"
    open "$PC_URL"
    osascript -e 'display notification "PC 与平板访问服务已经就绪" with title "Puricore 实验系统"' >/dev/null 2>&1
    wait "$APP_PID"
    APP_EXIT_CODE=$?
    cleanup_pid_file
    trap - HUP INT TERM
    print ""
    print "Puricore 实验系统已停止。"
    exit "$APP_EXIT_CODE"
  fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    cleanup_pid_file
    print ""
    print "应用进程提前退出，最近日志如下："
    tail -n 30 "$LOG_FILE"
    fail "请根据上面的日志检查配置。"
  fi
  sleep 1
done

kill -TERM "$APP_PID" 2>/dev/null || true
cleanup_pid_file
fail "等待 20 秒后服务仍未就绪，请查看日志：$LOG_FILE"
