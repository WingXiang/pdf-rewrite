# 使用 Python 3.10 輕量版作為基礎映像檔
FROM python:3.10-slim

# 設定環境變數
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 設定工作目錄
WORKDIR /app

# 安裝作業系統依賴套件 (PaddleOCR 圖像處理與編譯所需)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先複製並安裝 requirements.txt 以利用 Docker 快取層
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 在建置階段預先下載繁中 PaddleOCR 輕量化模型，實現啟動免重複下載優化
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='chinese_cht', show_log=False)"

# 複製其餘專案程式碼
COPY . /app

# 確保快取與掛載資料夾存在
RUN mkdir -p /app/data/uploads /app/data/exports

# 暴露服務連接埠 (Hugging Face Spaces 規定必須使用 7860)
EXPOSE 7860

# 使用 Uvicorn 啟動全端服務
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
