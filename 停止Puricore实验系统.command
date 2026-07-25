#!/bin/zsh

set -u

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

PID_FILE="$SCRIPT_DIR/data/puricore-shortcut.pid"

notify() {
  print "$1"
  osascript -e "display notification \"$1\" with title \"Puricore 实验系统\"" >/dev/null 2>&1
}

if [[ ! -f "$PID_FILE" ]]; then
  notify "没有发现由快捷方式启动的系统进程，无需停止。"
  exit 0
fi

APP_PID="$(tr -cd '0-9' < "$PID_FILE")"
if [[ -z "$APP_PID" ]]; then
  rm -f "$PID_FILE"
  notify "启动记录无效，已清理；没有停止任何进程。"
  exit 0
fi

COMMAND="$(ps -p "$APP_PID" -o command= 2>/dev/null || true)"
if [[ -z "$COMMAND" ]]; then
  rm -f "$PID_FILE"
  notify "系统已经停止，已清理旧的启动记录。"
  exit 0
fi

if ! print -r -- "$COMMAND" | grep -q -- "-m lab_device_manager"; then
  rm -f "$PID_FILE"
  notify "PID 已被其他程序使用，为安全起见没有停止该进程。"
  exit 1
fi

print "正在停止 Puricore 实验系统（PID $APP_PID）……"
kill -TERM "$APP_PID"
for attempt in {1..10}; do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    notify "Puricore 实验系统已安全停止。"
    exit 0
  fi
  sleep 1
done

notify "进程未在 10 秒内退出；没有强制结束，请查看终端或活动监视器。"
exit 1
