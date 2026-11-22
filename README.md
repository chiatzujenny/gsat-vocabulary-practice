# 學測英文單字練習

以 Streamlit 建立的單字選擇題練習網站，包含隨機出題、中文解析與錯題重練。題目為練習題，並非官方歷屆試題。

## 執行方式

在此資料夾開啟終端機，依序輸入：

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

瀏覽器通常會自動開啟；若沒有，請開啟終端機顯示的 Local URL。測驗進度存在目前瀏覽器工作階段，重新整理或關閉後可能遺失。

## 編輯題庫

`questions.json` 是 UTF-8 編碼的 JSON 陣列。每題需要 `question`、四個不同的 `options`、`answer`（須與一個選項完全相同）、`explanation` 和 `category`。題庫數量變更後，側邊欄的最後一個題數選項會跟著更新。
