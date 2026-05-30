import io
import os
import uuid
import logging
import numpy as np
from PIL import Image

logger = logging.getLogger("ocr_engines")

# ============================================================
# 嘗試載入各 OCR 引擎
# ============================================================

# 1. EasyOCR (首選 - 繁體中文支援佳，純 Python 安裝)
EASYOCR_AVAILABLE = False
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    logger.warning("EasyOCR library not found.")

# 2. PaddleOCR (備選)
PADDLE_OCR_AVAILABLE = False
try:
    from paddleocr import PaddleOCR
    PADDLE_OCR_AVAILABLE = True
except ImportError:
    logger.warning("PaddleOCR library not found.")


class EasyOCREngine:
    """
    EasyOCR 引擎封裝 - 支援繁體中文 (ch_tra) 與英文 (en)。
    首次初始化時會自動下載模型檔案到本地快取。
    """
    def __init__(self):
        self.reader = None
        self._initialized = False

    def _ensure_initialized(self):
        if not self._initialized:
            logger.info("Initializing EasyOCR reader with languages: ch_tra, en ...")
            self.reader = easyocr.Reader(
                ['ch_tra', 'en'],
                gpu=False,  # CPU 模式，確保在任何環境下可用
                verbose=False
            )
            self._initialized = True
            logger.info("EasyOCR reader initialized successfully.")

    def process_image(self, file_bytes: bytes, filename: str = "") -> list[dict]:
        """
        對圖片執行 OCR，回傳帶有 bounding box 的文字區塊列表。
        """
        self._ensure_initialized()

        # 將 bytes 轉為 numpy array (EasyOCR 接受 numpy array 或檔案路徑)
        image = Image.open(io.BytesIO(file_bytes))
        if image.mode != 'RGB':
            image = image.convert('RGB')
        img_array = np.array(image)

        # 執行 OCR
        # result 格式: [ ([x0,y0],[x1,y1],[x2,y2],[x3,y3]), text, confidence ]
        results = self.reader.readtext(img_array)

        blocks = []
        for idx, (bbox, text, confidence) in enumerate(results):
            text = text.strip()
            if not text:
                continue

            # bbox 是四個角點座標 [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]
            x_min = int(min(x_coords))
            y_min = int(min(y_coords))
            x_max = int(max(x_coords))
            y_max = int(max(y_coords))

            blocks.append({
                "blockId": f"b-ocr-{idx+1}",
                "type": "text",
                "boundingBox": [x_min, y_min, x_max, y_max],
                "text": text,
                "confidence": round(confidence, 4)
            })

        logger.info(f"EasyOCR extracted {len(blocks)} text blocks from image.")
        return blocks


class GoogleCloudVisionOCR:
    """
    Google Cloud Vision API 介面實作範例。
    使用者可透過設定 API 金鑰或環境變數 GOOGLE_APPLICATION_CREDENTIALS 來啟用此模組。
    """
    def __init__(self, credentials_path: str = None):
        self.credentials_path = credentials_path
        self.client = None

    def process_image(self, file_bytes: bytes) -> list[dict]:
        if not self.client:
            raise ValueError("Google Cloud Vision client is not initialized.")
        return []


class AzureVisionOCR:
    """
    Azure AI Vision (Read API) 介面實作範例。
    """
    def __init__(self, endpoint: str = None, subscription_key: str = None):
        self.endpoint = endpoint
        self.subscription_key = subscription_key

    def process_image(self, file_bytes: bytes) -> list[dict]:
        return []


class OCRManager:
    def __init__(self, mode: str = "auto", config: dict = None):
        """
        mode:
          'auto'       - 自動選擇可用引擎 (優先: EasyOCR > PaddleOCR > Mock)
          'easyocr'    - 強制使用 EasyOCR
          'paddleocr'  - 強制使用 PaddleOCR
          'google'     - Google Cloud Vision
          'azure'      - Azure AI Vision
        """
        self.mode = mode
        self.config = config or {}
        self.paddle_ocr = None
        self.easyocr_engine = None

        if self.mode == "easyocr" or (self.mode == "auto" and EASYOCR_AVAILABLE):
            self.easyocr_engine = EasyOCREngine()
            self.active_engine = "easyocr"
            logger.info("OCR Manager: Using EasyOCR engine (繁體中文 + English).")

        elif self.mode == "paddleocr" or (self.mode == "auto" and PADDLE_OCR_AVAILABLE):
            try:
                self.paddle_ocr = PaddleOCR(use_angle_cls=True, lang="chinese_cht", show_log=False)
                self.active_engine = "paddleocr"
                logger.info("OCR Manager: Using PaddleOCR engine.")
            except Exception as e:
                logger.error(f"Failed to initialize PaddleOCR: {e}. Falling back to Mock.")
                self.active_engine = "mock"

        elif self.mode == "google":
            self.active_engine = "google"
            self.engine = GoogleCloudVisionOCR(self.config.get("google_credentials"))
        elif self.mode == "azure":
            self.active_engine = "azure"
            self.engine = AzureVisionOCR(self.config.get("azure_endpoint"), self.config.get("azure_key"))
        else:
            self.active_engine = "mock"
            logger.info("OCR Manager: No OCR engine available. Using Mock fallback.")

    def process_image(self, file_bytes: bytes, filename: str) -> list[dict]:
        """
        統一調用接口。依據 active_engine 解析圖片並回傳文字區塊列表。
        """
        if self.active_engine == "easyocr" and self.easyocr_engine:
            try:
                return self.easyocr_engine.process_image(file_bytes, filename)
            except Exception as e:
                logger.error(f"EasyOCR process error: {e}. Falling back to mockup.")
                return self._get_mockup_blocks(800, 1000, filename)

        elif self.active_engine == "paddleocr" and self.paddle_ocr:
            try:
                result = self.paddle_ocr.ocr(file_bytes, cls=True)
                blocks = []
                if result and result[0]:
                    for idx, line in enumerate(result[0]):
                        coords = line[0]
                        text, confidence = line[1]
                        x_coords = [p[0] for p in coords]
                        y_coords = [p[1] for p in coords]
                        x_min, x_max = min(x_coords), max(x_coords)
                        y_min, y_max = min(y_coords), max(y_coords)
                        blocks.append({
                            "blockId": f"b-{idx+1}",
                            "type": "text",
                            "boundingBox": [int(x_min), int(y_min), int(x_max), int(y_max)],
                            "text": text,
                            "confidence": round(confidence, 4)
                        })
                return blocks
            except Exception as e:
                logger.error(f"PaddleOCR process error: {e}. Falling back to mockup.")
                return self._get_mockup_blocks(800, 1000, filename)

        elif self.active_engine == "google":
            return self.engine.process_image(file_bytes)
        elif self.active_engine == "azure":
            return self.engine.process_image(file_bytes)
        else:
            return self._get_mockup_blocks(800, 1000, filename)

    def _get_mockup_blocks(self, w: int, h: int, filename: str) -> list[dict]:
        """
        本地 OCR 模擬分析器 (fallback)。
        """
        blocks = []
        blocks.append({
            "blockId": "b-mock-1",
            "type": "text",
            "boundingBox": [int(w * 0.1), int(h * 0.15), int(w * 0.9), int(h * 0.38)],
            "text": f"[Mock OCR] 無可用的 OCR 引擎。此為模擬文字，非真實辨識結果。檔案: {filename}"
        })
        return blocks
