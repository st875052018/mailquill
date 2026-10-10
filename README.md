<p align="center">
  <img src="docs/hero.svg" alt="Mailquill — your bank statements, quietly reconciled. 100% local, zero cloud." width="820">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/processing-100%25%20local-46c08a" alt="100% local">
  <img src="https://img.shields.io/badge/cloud%2FLLM-zero-e5484d" alt="Zero cloud / LLM">
  <img src="https://img.shields.io/badge/license-MIT-c99a52" alt="MIT license">
</p>

<p align="center"><b>把散落在 Gmail 裡的銀行／信用卡帳單，安靜地收攏成一本私密、離線的財務帳。</b></p>

**Mailquill** 讀取你 Gmail 中的台灣銀行／信用卡財務信件（電子帳單、消費通知、收據），解密 PDF、用**純規則**解析成結構化交易、做兩層分類，寫入 CSV（唯一真實來源），再產生一份**自包含的離線 HTML 儀表板**，讓你核對消費紀錄／金流／類別統計。

> 🔒 **全本地處理**：純 rule-based，**零 LLM、零雲端**；Gmail 僅唯讀存取。你的帳單、金額、卡號從不離開這台電腦。

---

## ✨ 特色

- **收件匣直接變帳本** — 依你在 Gmail 已分類的 Label 抓信，自動辨識銀行、解析明細。
- **解密加密 PDF** — 用你本地的密碼清單（`passwords.txt`）自動嘗試解鎖電子帳單。
- **純規則、可稽核** — 每家銀行一支 parser，正則對版型；不猜、不呼叫任何模型，結果可複現。
- **兩層分類、隨你調** — `關鍵字 → (第一層, 第二層)`，大小寫／全半形不敏感；改完一鍵重分類。
- **離線互動儀表板** — 單一 HTML 檔，零外部資源；可依銀行／分類／月份篩選，支援深色主題、正負金額紅綠標示、未分類整理面板。
- **永不靜默丟資料** — 無對應 parser、PDF 解不開、金額解析失敗，都會列成清單讓你逐步補齊。

---

## 📸 畫面預覽

<p align="center">
  <img src="assets/images/demo-2.png" alt="總覽 — KPI 卡與月度趨勢（深色主題）" width="840"><br>
  <sub><b>總覽</b>：淨消費／筆數／月均 + 月度趨勢；一鍵切換深色主題，分類／品類支援階層勾選（虛線＝部分選取）。</sub>
</p>

<p align="center">
  <img src="assets/images/demo-1.png" alt="分類拆解 — 第一層圓餅與第二層品類長條" width="840"><br>
  <sub><b>分類拆解</b>：第一層占比圓餅 + 第二層品類 Top，長條／圓餅可切換。</sub>
</p>

<p align="center">
  <img src="assets/images/demo-3.png" alt="分類拆解 — 排除模式與跨銀行篩選" width="840"><br>
  <sub><b>負向表列</b>：品類「排除」模式（紅色刪除線）＋跨銀行篩選。</sub>
</p>

---

## 🚀 快速開始

```bash
python3 -m venv .venv
source .venv/bin/activate          # 之後所有指令都在這個 venv 裡跑
pip install -e .                   # 安裝
pip install -e ".[dev]"            # 含測試相依(選用)

# HSBC 帳單商家名稱是內嵌圖片、需 OCR 才能解析，額外裝系統 tesseract：
brew install tesseract tesseract-lang         # macOS；只用不到 HSBC 可略過

# 1) 設定 Google OAuth、config.yaml、passwords.txt（見 docs/SETUP.md）
# 2) 從既有 Gmail Label 產生抓取規則草稿，檢查後即可
mailquill bootstrap

# 3) 抓信 → 解密 → 解析 → 分類 → 寫入 CSV / 重建 DB
mailquill run

# 4) 產生離線 HTML 儀表板
mailquill report        # → report.html，用瀏覽器打開
```

> 完整的 Google Cloud OAuth 申請、設定檔與密碼清單步驟，請見 **[設定指南 · docs/SETUP.md](docs/SETUP.md)**。
> `mailquill` 與 `python -m mailquill.cli` 等價。

---

## 📅 每月例行：一個指令就好

```bash
mailquill update
```

它會依序做三件事，**其他子指令平常都不用記**：

1. `run` — 抓 Gmail 新信件 → 解析 → 分類 → 更新 CSV / SQLite
2. `reconcile` — 合併每日通知與月結單對同一筆消費的重複記錄
3. `sync-sheets` — 把結果推上 Google Sheet（手機用 Sheets App 看）

跑完會印一行總結（新增幾筆、合併幾筆、Sheet 網址）。中間若出現「待補 parser / 解析警告 / 抓取失敗」，處理完再跑一次即可，已入帳的信不會重複。

需要手動下載帳單的銀行（富邦 PDF、Amex CSV）例外：先 `mailquill ingest <檔案>` 匯入，再跑一次 `mailquill update` 同步上去。

想確認每家銀行的最新帳單有沒有都進來，跑 `mailquill status`（只讀本地資料、不連 Gmail）：列出各銀行最新帳單月份、月結單交易／每日通知各到哪一天，落後的會標 ⚠️／❌。

---

## 🔁 一次抓信，之後只重建／重報表（不用一直重抓）

Mailquill 的核心是 **CSV 是唯一真實來源（single source of truth）**，SQLite 與 HTML 報表都只是它的衍生產物、隨時可丟可重建。這讓大多數日常操作**完全不需要再連 Gmail**：

| 你想做的事 | 指令 | 會不會連 Gmail？ |
|---|---|---|
| 納入新到的帳單信 | `mailquill run` | ✅ 需要（只有這件事需要） |
| 改了分類規則，想重新歸類 | `mailquill rebuild` | ❌ 只讀 CSV → 重新分類 → 重建 DB |
| 換報表視角／樣式、重出儀表板 | `mailquill report` | ❌ 只讀 DB → 產生 HTML |
| SQLite 不小心壞了／刪了 | `mailquill rebuild` | ❌ 由 CSV 完整重建 |

```
                       ┌──────────── 只有這一步碰 Gmail ────────────┐
  Gmail ──(唯讀抓信)──▶ run ──▶ 解密PDF ──▶ 各家 parser ──▶ 正規化 ──▶ 分類
                                                                   │
                                                                   ▼
                                                    transactions.csv  ◀── 唯一真實來源(去重)
                                                          │
                        rebuild(改了 categories.yaml)  ◀──┤
                                                          ▼
                                                    mailquill.db(可重建的查詢層)
                                                          │
                                                       report
                                                          ▼
                                                    report.html(離線儀表板)
```

**實務上**：第一次 `run` 之後，你多半只會在調分類時跑 `rebuild`、想看不同切面時跑 `report`。抓信是最慢、最耗額度的一步——把它跟「分類／出表」解耦，就能反覆微調而不必重抓。

---

## 🔄 日常重跑 SOP（含 token 失效處理）

隔一段時間回來想「把新帳單補進來、重出報表」，照這三步走：

```bash
# 1) 抓新信（只有這步碰 Gmail）。--since 帶「上次抓到哪」的前一兩天即可
mailquill run --since 2026-06-01

# 2) 重新分類 + 重建 SQLite（路徑取自 config.yaml，不用自己帶 --db）
mailquill rebuild

# 3) 出離線儀表板
mailquill report      # → report.html
```

跑完先自我驗證：

```bash
pytest -q                                  # 全測試（<1 秒）
tail -1 transactions.csv | cut -d, -f2                          # CSV 最新一筆交易日
```

### ⏱ 增量掃描：`--since` 就是 startFrom

`run` 與 `bootstrap` **預設不會從頭掃**：

| 寫法 | 實際掃描範圍 |
|---|---|
| `run`（不帶參數） | **今年 1/1 之後**（`datetime.now().year`-01-01） |
| `run --since 2026-06-01` | 2026/6/1 之後（轉成 Gmail 查詢 `after:2026/06/01`） |
| `run --all` | 全部歷史（慢、耗額度，平常別用） |

- 日期格式必須 `YYYY-MM-DD`，寫錯會**直接報錯退出**，不會無聲退回成全歷史掃描。
- **沒有自動記住上次抓到哪**（不存 watermark），每次由你給 `--since`。
- **重疊掃描是安全的**：CSV 以交易指紋去重，同一筆重抓只會計入 `skipped`，不會產生重複列。所以 `--since` 抓寬一點（往前多幾天）比抓太窄好。
- 想知道現在資料到哪一天：`tail -1 transactions.csv | cut -d, -f2`，把那天當 `--since` 就行。

### 🔑 token 失效：`invalid_grant: Bad Request`

`token.json` 的 refresh token 過期或被撤銷時，`run` 會噴：

```
google.auth.exceptions.RefreshError: ('invalid_grant: Bad Request', ...)
```

處理方式是重新授權一次（會開瀏覽器，需人工點選，無法在自動化流程裡完成）：

```bash
mv token.json token.json.bak                                   # 留個備份，成功後可刪
mailquill run --since 2026-06-01        # 自動開瀏覽器授權
```

授權成功會自動寫回新的 `token.json`，然後接著跑完抓信流程。若想先只驗證授權有沒有通、不跑整條 pipeline：

```bash
mailquill labels        # 只列 Gmail Label，最輕量的連線測試
```

常見原因：refresh token 閒置超過 6 個月、Google Cloud 專案還在「測試中」（testing）狀態的 token 7 天到期、帳號改密碼、或在 Google 帳號設定裡移除了這個 App 的存取權。

### 🧬 `migrate-ids`：修舊式 txn_id 造成的重複列

`txn_id` 的雜湊公式後來把 `seq`（同一份帳單內的出現序號）納進去，讓「同日、同店、同額」的兩筆真實交易不會被誤判成同一筆。但公式改動前寫進 CSV 的列沒有一併重算，於是同一封信被重抓時，同一筆交易會拿到兩種 id，去重（只比對 `txn_id`）就攔不住，報表上出現兩筆一模一樣的紀錄。

```bash
mailquill migrate-ids            # dry-run：只列出會怎麼改，不寫檔
mailquill migrate-ids --apply    # 實際遷移（自動備份 + 重建 SQLite）
```

`--apply` 會先把原 CSV 複製成 `transactions.csv.bak-<時間戳>` 再原子寫回，並同步重建 SQLite。已遷移過的 CSV 再跑是 no-op，不會動檔。

`seq` 由「該列在其 `(source_msg_id, imported_at)` 群組內的位置」還原 —— 同一封信的交易是照解析順序寫進 CSV，同一份帳單重跑的解析順序一致，所以位置就是當初的 `seq`。`無法歸類的列` 應該是 0；不是 0 的話那些列會原封不動保留並列出來，不會被猜測性地改掉。

`seq` 現在是 CSV 的一個欄位，日後公式再變才有辦法重算。`run` 開跑前會檢查 CSV 裡還有沒有舊式 id（`seq` 為空），有就先警告你跑 `migrate-ids`，免得抓完才發現又多了重複列。

---

### 📁 路徑一律以 `config.yaml` 為準

`rebuild`、`report`、`run`、`ingest` 都讀同一份 `config.yaml`（預設 `./config.yaml`，可用 `--config` 指定），所以 `csv_path` / `db_path` / `categories_path` 改一個地方就好，不會出現「rebuild 成功但報表沒更新」。

```bash
mailquill rebuild
# rebuild: 重新分類並重建 SQLite 中…（csv=transactions.csv db=mailagent.db categories=categories.yaml）
```

指令會把實際採用的路徑印出來，對不上時一眼看得到。想臨時蓋掉某個路徑（例如產一份測試用 DB）再帶旗標，旗標優先於 `config.yaml`：

```bash
mailquill rebuild --db /tmp/scratch.db
```

沒有 `config.yaml` 時（例如剛 clone、CI）`rebuild` 仍可跑，退回內建預設 `transactions.csv` / `mailquill.db` / `categories.yaml`。

---

## 🏦 支援的銀行

| 銀行 | parser 代號 | 帳單來源 | 狀態 |
|---|---|---|---|
| 國泰世華 Cathay | `cathay` | Gmail 附件 PDF | ✅ |
| 聯邦銀行 UBOT | `ubot` | Gmail 內文／附件 | ✅ |
| 台新 Taishin / Richart | `taishin` | Gmail 內文／附件 | ✅ |
| 玉山銀行 E.SUN | `esun` | Gmail 附件 PDF | ✅ |
| 永豐銀行 SinoPac | `sinopac` | Gmail 附件 PDF | ✅ |
| 滙豐(台灣) HSBC | `hsbc` | Gmail 附件 PDF（商家名稱靠 OCR，需系統裝 tesseract） | ✅ |
| 富邦銀行 Fubon | `fubon` | ⚠️ 需手動下載 PDF | ✅ parser 就緒 |

### ⚠️ 富邦：目前需手動下載後本地解析

富邦的電子帳單不以附件寄送，而是信中放一條外部連結，且下載前要在網站上填**身分證字號／生日／驗證碼**，無法自動抓取。因此流程是：

1. 依帳單信件的連結，自行到富邦網站下載該期 PDF。
2. 用本地匯入指令解析（不經 Gmail）：

```bash
mailquill ingest --bank fubon 115年02月.pdf      # 單一檔
mailquill ingest --bank fubon ~/Downloads/fubon/  # 整個資料夾
```

匯入的交易一樣寫進 `transactions.csv`、參與去重與分類，和自動抓取的資料完全一致。

---

## 🤝 一起把更多銀行開源進來

台灣的銀行帳單版型五花八門，一個人補不完。**如果你手上有某家銀行的帳單、願意貢獻一支 parser，非常歡迎送 PR。** 架構刻意做成「一家銀行一支模組、靠統一 schema 與其他部分隔離」，新增一家通常只要：

1. 複製 `mailquill/parsers/example_bank.py`。
2. 寫 `matches()`（比對該行寄件網域）與 `parse()`（針對版型的正則）。
3. 附上 **合成（去識別化）** 的測試，讓明細總和對得起來。
4. 在 `parsers/__init__.py` 註冊。

完整步驟、schema 說明與 **去識別化規範** 請見 **[貢獻指南 · CONTRIBUTING.md](CONTRIBUTING.md)**。

> 🙏 送測試資料前請務必用假商家／假卡號／假金額——真實帳單資料不該進 repo。

---

## 🗂 分類規則

`categories.yaml`：`關鍵字 → (第一層, 第二層)`。由上而下取第一個「關鍵字出現在商家名稱內」者命中；命中不到歸「未分類」。比對前會做 Unicode 正規化（NFKC）與大小寫摺疊（casefold），所以 `Uber`／`UBER`／全形字視為相同。隨時手動編輯，改完跑 `mailquill rebuild` 即全部重新分類。

儀表板的 **「未分類整理」面板** 可讓你在 UI 上為未分類商家指定分類，再匯出成可貼回 `categories.yaml` 的規則——調一次，之後 `rebuild` 就永遠記得。

---

## 📱 手機檢視：同步到 Google Sheet（選用，非本機）

想在手機上用 Sheets App 隨手看，可以把 CSV 整份鏡像到一份 Google Sheet：

```bash
mailquill sync-sheets
```

第一次執行會自動建立一份新試算表，把網址存進本地 `sheets_state.json`（已列入 `.gitignore`）並印出來；之後每次執行都整份清空重寫同一份試算表（Sheet 只是唯讀鏡像，CSV 仍是唯一真實來源）。每次同步都會產生：

分頁由左到右依序是 `總覽`、`預算`、`明細查詢`、`交易`，以及預設隱藏的 `歸檔`（舊版的英文分頁名稱會在同步時原地改名，預算裡填的數字不會消失）：

- `總覽`：單一分頁，依幣別各疊一段「月份 x 帳戶」加總矩陣（中間空一列隔開，如 `月份 (TWD)`、`月份 (USD)`），含月合計與帳戶合計，一眼看出這個月每張卡大概要繳多少；只含 2026 年起的月份，跟 `交易` 一致（更舊的月度總覽不重複列出）。刻意仍按幣別分段、不把數字加在一起——不同幣別不能直接相加，但放同一個分頁方便手機上不用切分頁就能都看到。矩陣最下面（隔一空列）會疊一段「本期應繳」小表，列出有支援的銀行（目前只有 HSBC）最新一期的應繳總額/最低應繳，讀獨立的 `bills.csv`（見下方「帳單應繳金額」）；沒有這類資料就不出現。
- `預算`：分類／月預算／近3月平均／實際／差異五欄合一（預算、近3月平均參考、跟實際花費的比對都在同一張表，不用切分頁對照）。分類清單＋空白的月預算欄只在第一次執行時建立，之後每次同步都**不會**再清空重寫——自己在 Sheets App 上填每個分類的月預算即可，填的數字永遠不會被洗掉；近3月平均/實際/差異則每次同步都重新算重新寫，讀「近期資料裡最新一個月」的實際分類花費跟預算比對，差異＝實際－預算（正值代表超支，標紅字；負值代表省下，標綠字）。只取一種幣別（近期交易筆數最多的那個）。
- `明細查詢`：上方兩個下拉選單——月份、帳戶（都可選「全部」），下面即時列出符合的交易；帳戶名稱套用 `config.yaml` 的 `account_aliases`。原本每個帳戶各開一個分頁，內容跟交易總表重複，改成在這裡用選單篩。
- `交易`：2026 年起（含）的交易合併總表，最後一欄 `account` 是帳戶名稱，可直接用表頭篩選器篩。
- `歸檔`（預設隱藏）：2026 年以前的所有交易，欄位跟 `交易` 一樣；需要時從「檢視 > 隱藏的工作表」叫出來。

> 2026 這個斷點目前寫死在 `mailquill/sheets_sync.py` 的 `ARCHIVE_CUTOFF`，要調整（例如往後留兩年）改那一行常數即可。

新增卡片/新月份/新幣別時，下拉選單（或總覽裡的段落）會自動補上；改版前殘留的舊分頁（如曾經拆開的 `Overview-TWD`、以前的各帳戶分頁）會被刪除；其餘沿用同一個 sheetId，不會整份重建。

> ⚠️ **這個功能會把交易資料寫進 Google 雲端**，跟本專案其餘功能「純本地」不同——請自行評估是否要用。它跟 Gmail 抓信共用同一份 OAuth token（`token_path`），需要 `spreadsheets` 這個額外 scope；若 `token.json` 是舊版只授權過 Gmail，刪掉它讓下次執行任一指令重新走一次授權（會一次跳出兩個權限請求）即可。

### 帳單應繳金額

逐筆消費（`transactions.csv`）之外，`mailquill run` 也會順便把信件裡帳單本身的「應繳總額」/「最低應繳」抓出來，寫進獨立的 `bills_path`（預設 `bills.csv`，已列入 `.gitignore`）——一筆代表一家銀行某個帳單月份，同一期重跑會用新值蓋掉舊值。目前只有 HSBC 支援：滙豐電子帳單信會附帶兩個超商繳費條碼 HTML（`eBarCodeFullPay.html`/`eBarCodeMinPay.html`），格式固定的純文字，比 PDF 彙總頁（中文標籤是內嵌小圖，OCR 不到）可靠。`sync-sheets` 會把每家銀行最新一期的應繳金額顯示在 `Overview` 分頁矩陣最下面。已經在 `transactions.csv` 裡的舊 HSBC 帳單信不會自動補抓這筆——需要 `mailquill run --refetch` 讓舊信重跑一次 parser。

---

## 🔐 隱私與安全

- 核心處理（`run`/`ingest`/`rebuild`/`report`/`serve`）都在本機；唯一的網路存取是 Gmail **唯讀** API。`sync-sheets` 是唯一例外，見上一節。
- 這些檔案 **永不進 git**（已列入 `.gitignore`）：
  `credentials.json`、`token.json`、`passwords.txt`、`config.yaml`、`rules.yaml`、`transactions.csv`、`*.db`、`report.html`、`*.pdf`、`raw/`、`sheets_state.json`。
- PDF 密碼放在本地 `passwords.txt`，不要貼到任何地方。

---

## 🧪 開發

```bash
pytest      # 全部測試
```

專案結構：`mailquill/`（核心）、`mailquill/parsers/`（各家 parser）、`tests/`、`docs/`。
資料流與元件邊界見上方架構圖與 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 📄 授權

[MIT](LICENSE)。
