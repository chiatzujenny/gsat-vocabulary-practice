# 學測英文單字練習

以 Streamlit 建立的單字選擇題練習網站，包含隨機出題、中文解析與錯題重練。題目為練習題，並非官方歷屆試題。

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

首次啟動會建立資料表。管理員登入後可為每位學生產生一組代碼；代碼只顯示一次，請安全交給學生。學生登入時填寫名稱和代碼，交卷後會保存姓名、分數及每題作答。相同代碼的紀錄會歸屬同一學生，名稱由學生自行填寫。代碼若遺失，重新產生的代碼不會自動合併舊紀錄。

## 編輯題庫

`questions.json` 是 UTF-8 編碼的 JSON 陣列。每題需要 `question`、四個不同的 `options`、`answer`（須與一個選項完全相同）、`explanation` 和 `category`。題庫數量變更後，側邊欄的最後一個題數選項會跟著更新。
