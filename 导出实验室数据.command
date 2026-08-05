#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

"$PYTHON_BIN" "$SCRIPT_DIR/scripts/export_data_bundle.py"
open "$HOME/Desktop"
echo
echo "已导出到桌面：puricore-data-transfer.zip"
echo "请把这个 ZIP 文件复制到 Windows 离线部署文件夹中。"
read "?按回车键关闭窗口。"
