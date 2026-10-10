"""mailquill CLI。已實作 `rebuild`、`bootstrap`、`run`；`report` 由後續計畫追加。"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime

from mailquill.categorizer import load_categories, upsert_rules
from mailquill.store import (
    count_legacy_id_rows, read_transactions, rebuild, rebuild_sqlite, reconcile,
    write_transactions_atomic,
)
from mailquill.bills_store import read_bills
from mailquill.status import bank_statuses, format_status
from mailquill.config import Config, load_config
from mailquill.bootstrap import bootstrap_rules
from mailquill.gmail_client import build_service, list_all_labels, gmail_after_query
from mailquill.rules import save_rules
from mailquill.pipeline import run_pipeline
from mailquill.ingest import ingest_paths, collect_csvs
from mailquill.migrate import plan_migration
from mailquill.amex_autocat import generate_rules
from mailquill.cathay_autocat import (
    fetch_merchant_categories, generate_rules as cathay_generate_rules,
)
from mailquill.report import generate_report
from mailquill.webapp import run_server
from mailquill.sheets_client import build_service as build_sheets_service
from mailquill.sheets_sync import sync_sheets


def _progress(label: str):
    """回傳一個進度回呼，在同一行更新 'label done/total'，完成時換行。"""
    def cb(done: int, total: int) -> None:
        end = "\n" if total and done >= total else ""
        sys.stdout.write(f"\r{label} {done}/{total}{end}")
        sys.stdout.flush()
    return cb


def _add_date_args(p) -> None:
    """為會抓 Gmail 的指令加上時間範圍選項。"""
    p.add_argument("--since", default=None,
                   help="只看此日期(含)之後的信，格式 YYYY-MM-DD；預設為今年 1/1")
    p.add_argument("--all", action="store_true",
                   help="不限時間，掃描全部歷史信件")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_since(args) -> str | None:
    """回傳要套用的起始日期字串；--all 時回 None（不過濾），否則預設今年 1/1。

    格式錯誤直接報錯退出，避免無聲退回成全歷史掃描。
    """
    if getattr(args, "all", False):
        return None
    since = args.since or f"{datetime.now().year}-01-01"
    if not _DATE_RE.match(since):
        raise SystemExit(f"--since 格式需為 YYYY-MM-DD（收到：{since}）")
    return since


def _config_or_defaults(path: str) -> Config:
    """讀 config.yaml 當作路徑預設值；檔案不存在或缺 label 時退回 Config 預設。

    rebuild 完全離線（只碰 CSV/SQLite），沒有 config.yaml 也該能跑。
    """
    try:
        return load_config(path)
    except (FileNotFoundError, ValueError):
        return Config(label=[])


def _warn_legacy_ids(csv_path: str) -> int:
    """CSV 裡還有舊式 id 就警告：重抓同一封信會產生重複列。"""
    n = count_legacy_id_rows(csv_path)
    if n:
        print(f"⚠ CSV 有 {n} 列是舊式 txn_id（無 seq）。重抓到同一封信會產生"
              f"重複列，建議先執行：mailquill migrate-ids --apply")
    return n


def _migrate_ids(cfg_path: str, apply: bool) -> int:
    """重算舊式 txn_id、補上 seq、移除因此撞成同 id 的重複列。"""
    cfg = load_config(cfg_path)
    txns = read_transactions(cfg.csv_path)
    plan = plan_migration(txns)

    mode = "apply" if apply else "dry-run"
    print(f"migrate-ids ({mode}): csv={cfg.csv_path}")
    print(f"  總列數                     : {len(txns)}")
    print(f"  id 重算的列                : {plan.changed}")
    print(f"  無法歸類的列（應為 0）      : {len(plan.unexplained)}")
    print(f"  重複而移除的列              : {len(plan.removed)}")
    print(f"  遷移後總列數                : {len(plan.txns)}")
    for t in plan.removed:
        print(f"    - {t.date} {t.amount:>9} {t.bank:<8} {t.merchant_raw} "
              f"（{t.txn_id} imported_at={t.imported_at}）")
    for t in plan.unexplained:
        print(f"    ? 無法歸類：{t.date} {t.merchant_raw} {t.txn_id}")

    if not apply:
        print("  未寫檔。確認以上內容後加 --apply 才會實際遷移。")
        return 0
    if plan.changed == 0 and not plan.removed and plan.txns == txns:
        print("  無需遷移，未寫檔。")
        return 0

    backup = f"{cfg.csv_path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(cfg.csv_path, backup)
    write_transactions_atomic(cfg.csv_path, plan.txns)
    n = rebuild_sqlite(cfg.csv_path, cfg.db_path)
    print(f"  已遷移：備份 -> {backup}；重建 SQLite {n} 列 -> {cfg.db_path}")
    return 0


def _print_items(title: str, items: list, note: str = "") -> None:
    if not items:
        return
    print(f"{title}（{len(items)}{note}）：")
    for item in items:
        print(f"  - {item}")


def _write_new_rules(new_rules: list, categories_path: str, dry_run: bool) -> None:
    """列出 autocat 產生的新規則；dry_run 只印不寫，否則 upsert 進 categories.yaml。"""
    for r in new_rules:
        print(f"  {r.keyword} -> {r.l1}/{r.l2}")
    if dry_run:
        print("dry-run：未寫入，未實際套用")
    elif new_rules:
        upsert_rules(categories_path, new_rules)


def _do_cathay_autocat(cfg, service, since: str | None, categories_path: str,
                       dry_run: bool = False) -> int:
    """掃每日通知信、依國泰自帶的消費類別產生新規則並附加進
    `categories_path`（dry_run 時只印出不寫入）。回傳新增規則數，供呼叫端
    決定要不要接著 rebuild 套用。
    """
    print("cathay-autocat: 掃描每日通知信中…")
    merchant_categories = fetch_merchant_categories(
        service, cfg.label_names(), query=gmail_after_query(since))
    existing_rules = load_categories(categories_path)
    new_rules = cathay_generate_rules(merchant_categories, existing_rules)
    print(f"cathay-autocat: 範圍={since or '全部歷史'} "
          f"掃到商家數={len(merchant_categories)} 新規則={len(new_rules)}")
    _write_new_rules(new_rules, categories_path, dry_run)
    return len(new_rules)


def _do_run(cfg, service, args):
    """執行 run 主流程（抓信→解析→分類→寫檔）並列印統計與各類警告，回傳 RunResult。"""
    imported_at = datetime.now().isoformat(timespec="seconds")
    since = _resolve_since(args)
    r = run_pipeline(service, cfg, imported_at, query=gmail_after_query(since),
                     on_progress=_progress("處理信件"), force_refetch=args.refetch)
    print(f"run: 範圍={since or '全部歷史'} fetched={r.fetched} "
          f"matched={r.matched} already_seen={r.already_seen} "
          f"added={r.added} skipped={r.skipped}")
    if r.missing_labels:
        print(f"找不到 Gmail Label（{len(r.missing_labels)}）：{', '.join(r.missing_labels)}")
    _print_items("待補 parser", r.needs_parser)
    _print_items("PDF 解密失敗", r.unlock_failures)
    _print_items("解析警告", r.parse_warnings)
    _print_items("抓取失敗", r.fetch_failures, note="，已跳過，重跑可補回")
    return r


def _do_reconcile(csv_path: str, db_path: str):
    print("reconcile: 合併每日通知與月結單重複交易中…")
    r = reconcile(csv_path, db_path)
    print(f"reconcile: merged={r.merged} ambiguous={r.ambiguous} voided={r.voided}")
    return r


def _do_sync_sheets(cfg):
    service = build_sheets_service(cfg.credentials_path, cfg.token_path)
    print("sync-sheets: 同步中…")
    r = sync_sheets(cfg.csv_path, cfg.sheets_state_path, service, aliases=cfg.account_aliases,
                    goal_monthly_income=cfg.goal_monthly_income, bills_path=cfg.bills_path)
    if r.created:
        print(f"sync-sheets: 已建立新試算表 -> {r.spreadsheet_url}")
    else:
        print(f"sync-sheets: 已覆寫 -> {r.spreadsheet_url}")
    print(f"sync-sheets: rows={r.rows} archived_rows={r.archived_rows} "
          f"accounts={len(r.accounts)} budget_month={r.budget_month}")
    return r


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailquill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rebuild = sub.add_parser(
        "rebuild", help="重新套用分類並由 CSV 重建 SQLite"
    )
    p_rebuild.add_argument("--config", default="config.yaml")
    p_rebuild.add_argument("--csv", default=None,
                           help="預設取 config.yaml 的 csv_path")
    p_rebuild.add_argument("--db", default=None,
                           help="預設取 config.yaml 的 db_path")
    p_rebuild.add_argument("--categories", default=None,
                           help="預設取 config.yaml 的 categories_path")

    p_reconcile = sub.add_parser(
        "reconcile",
        help="合併每日通知（email_body）與月結單（pdf）對同一筆消費的重複記錄，"
             "並清掉從未真正請款的幽靈通知"
    )
    p_reconcile.add_argument("--csv", default="transactions.csv")
    p_reconcile.add_argument("--db", default="mailquill.db")

    p_labels = sub.add_parser(
        "labels", help="列出 Gmail 內所有 Label 的確切名稱（除錯／設定用）"
    )
    p_labels.add_argument("--config", default="config.yaml")

    p_bootstrap = sub.add_parser(
        "bootstrap", help="掃描 Gmail Label 產生 rules.yaml 草稿供確認"
    )
    p_bootstrap.add_argument("--config", default="config.yaml")
    _add_date_args(p_bootstrap)

    p_run = sub.add_parser(
        "run", help="抓取財務信件、解析、分類並更新 CSV/SQLite"
    )
    p_run.add_argument("--config", default="config.yaml")
    p_run.add_argument("--cathay-autocat", action="store_true",
                       help="完成後接著執行 cathay-autocat 補規則、有新規則就自動 rebuild")
    p_run.add_argument("--sync-sheets", action="store_true",
                       help="完成後接著執行 sync-sheets，一次抓信+同步 Google Sheet")
    p_run.add_argument("--refetch", action="store_true",
                       help="CSV 裡已有交易的信也強制重抓/重解析（改過 parser 想讓舊信重跑時用），"
                            "預設會跳過已入帳的信以節省 API/CPU")
    _add_date_args(p_run)

    p_update = sub.add_parser(
        "update",
        help="每月一鍵更新：抓信 → 合併重複 → 同步 Google Sheet（等於 run + reconcile + sync-sheets）"
    )
    p_update.add_argument("--config", default="config.yaml")
    p_update.add_argument("--refetch", action="store_true",
                          help="已入帳的信也強制重抓/重解析（改過 parser 想讓舊信重跑時用）")
    _add_date_args(p_update)

    p_status = sub.add_parser(
        "status", help="各銀行最新帳單／交易同步到哪裡，檢查有沒有漏抓最新帳單（只讀本地資料）")
    p_status.add_argument("--config", default="config.yaml")
    p_status.add_argument("--csv", help="預設讀 config 的 csv_path")
    p_status.add_argument("--bills", help="預設讀 config 的 bills_path")
    p_status.add_argument("--today", help="YYYY-MM-DD，預設今天（測試用）")

    p_ingest = sub.add_parser(
        "ingest",
        help="從本地 PDF/CSV 檔或資料夾匯入帳單（如富邦需手動下載 PDF、"
             "Amex 只能匯出 CSV 者）",
    )
    p_ingest.add_argument("paths", nargs="+",
                          help="PDF/CSV 檔或含 PDF/CSV 的資料夾")
    p_ingest.add_argument("--config", default="config.yaml")
    p_ingest.add_argument("--bank", default=None,
                          help="指定銀行 parser（如 fubon）；只影響 PDF，"
                               "不給則依內容自動判斷。CSV 目前固定用 Amex 格式")

    p_amex_autocat = sub.add_parser(
        "amex-autocat",
        help="依 Amex CSV 的 Category 欄位自動產生 categories.yaml 規則"
             "（給大量一次性美國商家用，避免逐筆手寫關鍵字）",
    )
    p_amex_autocat.add_argument("paths", nargs="+",
                                help="Amex CSV 檔或含 CSV 的資料夾")
    p_amex_autocat.add_argument("--categories", default="categories.yaml")
    p_amex_autocat.add_argument("--dry-run", action="store_true",
                                help="只預覽將新增的規則，不寫入 categories.yaml")

    p_cathay_autocat = sub.add_parser(
        "cathay-autocat",
        help="依國泰每日通知信自帶的消費類別自動產生 categories.yaml 規則"
             "（重新掃 Gmail 上的通知信，不影響已存的交易資料）",
    )
    p_cathay_autocat.add_argument("--config", default="config.yaml")
    p_cathay_autocat.add_argument("--categories", default="categories.yaml")
    p_cathay_autocat.add_argument("--dry-run", action="store_true",
                                  help="只預覽將新增的規則，不寫入 categories.yaml")
    _add_date_args(p_cathay_autocat)

    p_migrate = sub.add_parser(
        "migrate-ids", help="把舊式 txn_id 重算成現行公式並移除因此重複的列"
    )
    p_migrate.add_argument("--config", default="config.yaml")
    p_migrate.add_argument("--apply", action="store_true",
                           help="真的寫回 CSV（預設只做 dry-run 並列出變更）")

    p_report = sub.add_parser(
        "report", help="由 SQLite 產生自包含 HTML 報表"
    )
    p_report.add_argument("--config", default="config.yaml")
    p_report.add_argument("--out", default="report.html")

    p_serve = sub.add_parser(
        "serve", help="啟動本地可編輯的網頁 dashboard（編輯分類、手動記帳、長期趨勢）"
    )
    p_serve.add_argument("--config", default="config.yaml")
    p_serve.add_argument("--port", type=int, default=8420)

    p_sync_sheets = sub.add_parser(
        "sync-sheets",
        help="把 CSV 整份鏡像到 Google Sheet（手機用 Sheets App 檢視，"
             "會把資料寫進 Google 雲端，非本機處理）",
    )
    p_sync_sheets.add_argument("--config", default="config.yaml")

    args = parser.parse_args(argv)
    if args.command == "rebuild":
        cfg = _config_or_defaults(args.config)
        csv_path = args.csv or cfg.csv_path
        db_path = args.db or cfg.db_path
        categories_path = args.categories or cfg.categories_path
        print(f"rebuild: 重新分類並重建 SQLite 中…"
              f"（csv={csv_path} db={db_path} categories={categories_path}）")
        n = rebuild(csv_path, db_path, categories_path)
        print(f"rebuilt {n} transactions -> {db_path}")
        return 0
    if args.command == "reconcile":
        _do_reconcile(args.csv, args.db)
        return 0
    if args.command == "labels":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        print("讀取 Gmail Label 清單中…")
        names = list_all_labels(service)
        print(f"Gmail 共 {len(names)} 個 Label：")
        for n in names:
            print(f"  - {n}")
        return 0
    if args.command == "bootstrap":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        since = _resolve_since(args)
        rules, missing = bootstrap_rules(service, cfg.label_names(),
                                         query=gmail_after_query(since),
                                         on_progress=_progress("掃描寄件者"))
        save_rules(cfg.rules_path, rules)
        print(f"bootstrap: 範圍={since or '全部歷史'}，"
              f"找到 {len(rules.senders)} 個寄件網域 -> {cfg.rules_path}")
        if missing:
            print(f"找不到 Gmail Label（{len(missing)}）：{', '.join(missing)}")
        print("請檢查並編輯 rules.yaml 後再執行 run。")
        return 0
    if args.command == "run":
        cfg = load_config(args.config)
        _warn_legacy_ids(cfg.csv_path)
        service = build_service(cfg.credentials_path, cfg.token_path)
        r = _do_run(cfg, service, args)
        since = _resolve_since(args)
        if args.cathay_autocat:
            n = _do_cathay_autocat(cfg, service, since, cfg.categories_path)
            if n:
                print("cathay-autocat: 新規則已寫入，重新套用分類中…")
                rebuild(cfg.csv_path, cfg.db_path, cfg.categories_path)
        if args.sync_sheets:
            _do_sync_sheets(cfg)
        return 0
    if args.command == "update":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        run_r = _do_run(cfg, service, args)
        rec_r = _do_reconcile(cfg.csv_path, cfg.db_path)
        sync_r = _do_sync_sheets(cfg)
        print(f"update 完成：新增 {run_r.added} 筆，合併 {rec_r.merged} 筆，"
              f"Sheet 已更新 -> {sync_r.spreadsheet_url}")
        return 0
    if args.command == "status":
        if args.csv and args.bills:
            csv_path, bills_path = args.csv, args.bills
        else:
            cfg = load_config(args.config)
            csv_path, bills_path = args.csv or cfg.csv_path, args.bills or cfg.bills_path
        today = args.today or datetime.now().strftime("%Y-%m-%d")
        statuses = bank_statuses(read_transactions(csv_path), read_bills(bills_path), today)
        print(format_status(statuses, today))
        return 0
    if args.command == "ingest":
        cfg = load_config(args.config)
        imported_at = datetime.now().isoformat(timespec="seconds")
        r = ingest_paths(args.paths, cfg, imported_at, bank=args.bank,
                         on_progress=_progress("匯入 PDF"))
        print(f"ingest: files={r.files} added={r.added} skipped={r.skipped}")
        for line in r.parsed:
            print(f"  {line}")
        _print_items("無對應 parser", r.needs_parser)
        _print_items("解密失敗", r.unlock_failures)
        _print_items("解析警告", r.parse_warnings)
        return 0
    if args.command == "amex-autocat":
        csvs = collect_csvs(args.paths)
        existing_rules = load_categories(args.categories)
        new_rules = generate_rules(csvs, existing_rules)
        print(f"amex-autocat: files={len(csvs)} 新規則={len(new_rules)}")
        _write_new_rules(new_rules, args.categories, args.dry_run)
        if new_rules and not args.dry_run:
            print(f"已附加進 {args.categories}，接著跑 mailquill rebuild 套用")
        return 0
    if args.command == "cathay-autocat":
        cfg = load_config(args.config)
        service = build_service(cfg.credentials_path, cfg.token_path)
        since = _resolve_since(args)
        n = _do_cathay_autocat(cfg, service, since, args.categories, dry_run=args.dry_run)
        if n and not args.dry_run:
            print(f"已附加進 {args.categories}，接著跑 mailquill rebuild 套用")
        return 0
    if args.command == "migrate-ids":
        return _migrate_ids(cfg_path=args.config, apply=args.apply)
    if args.command == "report":
        cfg = load_config(args.config)
        print("report: 由 SQLite 產生 HTML 報表中…")
        out = generate_report(cfg.db_path, args.out)
        print(f"report: 已產生 {out}")
        return 0
    if args.command == "serve":
        cfg = load_config(args.config)
        url = f"http://127.0.0.1:{args.port}"
        print(f"serve: 啟動中 -> {url}（Ctrl+C 結束）")
        run_server(cfg, port=args.port)
        return 0
    if args.command == "sync-sheets":
        cfg = load_config(args.config)
        _do_sync_sheets(cfg)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
