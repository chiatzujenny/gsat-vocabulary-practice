# 學測英文單字練習

以 Streamlit 建立的「Jenny老師的英文練習網站」，提供詞彙、綜合測驗、文意選填、篇章結構與閱讀測驗。題目為原創練習題，並非官方歷屆試題。

## 執行方式

在此資料夾開啟終端機，依序輸入：

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

瀏覽器通常會自動開啟；若沒有，請開啟終端機顯示的 Local URL。開始練習前須先設定資料庫與管理員密碼。已交卷且儲存成功的結果保存在資料庫；尚未交卷或尚未儲存的進度只在目前工作階段。

## 設定作答紀錄

在 PostgreSQL 服務建立資料庫後，將以下設定放在本機 `.streamlit/secrets.toml`，或放在 Streamlit Community Cloud 的 App settings → Secrets。不要把真正的連線字串或密碼提交至 GitHub。連線字串需包含 `sslmode=require`（或驗證憑證的 `verify-ca` / `verify-full`）。

```toml
[progress]
database_url = "postgresql://使用者:密碼@主機/資料庫?sslmode=require"
admin_password = "自行設定至少24字元的隨機密碼"
```

首次啟動會建立資料表。管理員登入後可為每位學生產生一組代碼；代碼只顯示一次，請安全交給學生。學生以個人代碼登入，名稱為選填；重新登入時留空會沿用之前填過的名稱。首次未填名稱會顯示代碼識別名稱。交卷後會保存姓名、分數、每題作答及耗時。相同代碼的紀錄會歸屬同一學生，名稱由學生自行填寫。代碼若遺失，重新產生的代碼不會自動合併舊紀錄。

學生可在「練習進度」查看已完成題型。正式練習每回固定：詞彙 10 題、綜合 10 題（2 篇）、文意選填 10 題、篇章結構 4 題、閱讀測驗 12 題（3 篇）。題組不可拆散，文意選填和篇章結構每個選項限用一次。錯題重練另列，不計入固定題數。計時從開始本回到成功交卷為止，未交卷前重新整理或關閉頁面會失去工作階段進度。舊版紀錄保留，但沒有作答時間。

交卷後可下載本回 PDF；每個詞彙選項提供中文詞義、搭配、近義與反義資訊（詞語不一定有直接反義詞），並補充部分重點詞的例句及易混淆說明。文章題另列重點詞彙。PDF 使用 SIL Open Font License 授權的 Noto Sans TC 字型，授權見 `fonts/OFL.txt`。

## 編輯題庫

`questions.json` 是 UTF-8 編碼的 JSON 陣列。每題需要 `question`、四個不同的 `options`、`answer`（須與一個選項完全相同）、`explanation` 和 `category`。詞彙題固定每回隨機抽 10 題，新增題目不會改變每回題數。

`sections.json` 儲存文章與題組，`study_notes.json` 儲存補充詞彙。新增題組時要保持每篇共用選項的答案不重複，並讓題幹空格編號與題目一致；正式每回題數由 `quiz_engine.py` 設定。現有 100 題詞彙題保留在 `questions.json`。

## 測試

在本機安裝依賴後執行 `python -m unittest discover -s tests`。測試使用獨立的暫存 SQLite 資料庫，不會連接或更動正式學生成績。
