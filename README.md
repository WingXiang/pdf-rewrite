# PDF 錯誤偵測器 (PDF Rewrite)

一個結合 AI 大語言模型與深度光學字元辨識 (OCR) 的智慧文件校對系統。支援上傳 PDF、掃描圖檔與 Word 文件，自動抓出錯別字、排版遺漏與語意不順，並支援原汁原味的 PDF 覆蓋匯出，保留原始文件的背景與排版設計。

## 功能特色
- **AI 智慧錯字偵測**：串接 Google Gemini API，精準辨識錯字與標點符號錯誤，並支援跨段落自動組合辨識，不再因為排版斷行而產生誤判。
- **全頁圖片 PDF 支援**：打破一般 PDF 編輯器無法處理純圖片掃描檔的限制，內建 EasyOCR 強制解析所有頁面。
- **影像級原位覆蓋匯出**：匯出 PDF 時，系統能準確計算原圖上的文字座標，以「白色底塊覆蓋＋重新印字」的技術，保留講義原有之美編與插圖。
- **防止亂碼洗版**：內建自動亂碼過濾器，自動隱藏無法辨識的 `\ufffd` 亂碼字元。
- **IP 額度限制**：內建簡易的 API 速率限制機制，每日限制相同 IP 的校對次數（10次），保護您的 API 額度。

## 系統架構
本專案為前後端分離架構設計於一體的應用：
- **前端**：基於 Vue 3 + Tailwind CSS 的響應式單頁應用程式 (SPA)，實作於 `static/index.html`。
- **後端**：基於 Python FastAPI 實作。負責處理 PDF 拆解 (PyMuPDF)、影像文字辨識 (EasyOCR)、與 AI 模型溝通 (Google Generative AI API)，以及最後的檔案重新生成與匯出。

---

## 本地端開發與啟動教學

### 1. 安裝環境與相依套件
本專案需要 Python 3.10 以上版本。建議使用虛擬環境 (Virtual Environment) 來安裝相依套件。

```bash
# 建立虛擬環境
python -m venv venv

# 啟動虛擬環境 (Windows)
.\venv\Scripts\activate

# 啟動虛擬環境 (Mac/Linux)
source venv/bin/activate

# 安裝所需套件
pip install fastapi uvicorn python-multipart PyMuPDF python-docx easyocr pillow python-dotenv google-generativeai requests
```

### 2. 環境變數與 API 金鑰設定
本系統高度依賴 Google Gemini API。為保護您的金鑰不被外洩，請遵守以下步驟設定環境變數：

1. 複製專案中的範例環境變數檔：
   ```bash
   cp .env.example .env
   ```
2. 打開 `.env` 檔案，填寫您的 `GEMINI_API_KEY`：
   ```env
   GEMINI_API_KEY=AIzaSyYourApiKeyHere...
   ```
> **安全防護機制**：我們已經在 `.gitignore` 中設定忽略 `.env` 檔案。只要您不在程式碼中硬寫金鑰，您的 API 金鑰就絕對不會洩漏到 GitHub 等公共原始碼庫中。

### 3. 啟動伺服器
完成安裝與設定後，即可啟動 FastAPI 後端伺服器：

```bash
python -m uvicorn main:app --port 8000 --host 0.0.0.0 --reload
```

啟動後，請打開瀏覽器前往：`http://127.0.0.1:8000` 即可開始使用。

---

## 關於雲端部署 (部署至 GitHub Pages)

若您希望透過 **GitHub Pages** 免費代管前端頁面，請注意以下架構限制：
**GitHub Pages 僅支援純靜態檔案 (HTML/CSS/JS)，不支援執行 Python 後端程式**。

因此，若您使用本專案提供的 GitHub Action (`.github/workflows/deploy.yml`) 部署到 GitHub Pages 時：
1. **靜態網頁會成功部署**：您可以在 GitHub 提供的 `https://<您的帳號>.github.io/pdf-rewrite/` 網址看到漂亮的介面。
2. **API 呼叫會指向本地端**：預設情況下，前端會嘗試呼叫 `http://127.0.0.1:8000`。這代表當您（或其他人）打開 GitHub Pages 的網頁時，**您的電腦仍然需要執行 `python -m uvicorn main:app` 才能正常上傳檔案與執行校對**。

> 💡 **進階部署建議**：若您希望系統能「24 小時完全在雲端運作」且不依賴您的個人電腦，請考慮將整個專案（包含 Python 後端）部署至 **Render**、**Railway** 或 **Heroku** 等支援 Python 的雲端應用平台，並在雲端平台的後台面板中設定 `GEMINI_API_KEY` 環境變數。
