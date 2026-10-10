#!/bin/sh
# 一鍵更新：抓 Gmail 新信 → 合併重複 → 同步 Google Sheet → 顯示各銀行同步狀態。
# 在任何目錄都能跑：./update.sh（可帶參數，如 ./update.sh --since 2026-09-01）
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python -m mailquill.cli update "$@"
