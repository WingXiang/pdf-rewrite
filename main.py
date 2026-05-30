from dotenv import load_dotenv
load_dotenv()

import io
import os
import uuid
import logging
import urllib.parse
import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import fitz  # PyMuPDF
import docx  # python-docx
from PIL import Image

from ocr_engines import OCRManager
from export_service import export_to_docx, export_to_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main_server")

app = FastAPI(title="PDF Editor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ocr_manager = OCRManager(mode="auto")

FILE_CACHE = {}
TASKS_DB = {}

MAX_OCR_PAGES = 30

IP_RATE_LIMIT = {}
RATE_LIMIT_DATE = datetime.date.today()

os.makedirs("./data/uploads", exist_ok=True)
os.makedirs("./data/exports", exist_ok=True)
os.makedirs("./static/extracted_images", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.get("/health")
def health_check():
    return {"status": "ok", "ocr_engine": "easyocr", "version": "2.0.0"}

class PdfExportRequest(BaseModel):
    fileId: str
    corrections: list
    paragraphs: list = []

class RedetectRequest(BaseModel):
    paragraphs: list
    mode: str = "all"

def process_file_background(task_id: str, file_path: str, filename: str):
    try:
        pdf_doc = fitz.open(file_path)
        total_pages = len(pdf_doc)
        pages = []
        img_dir = "./static/extracted_images"
        
        pages_to_process = total_pages
        MAX_OCR_PAGES = 10
        
        for page_idx in range(pages_to_process):
            page = pdf_doc[page_idx]
            width = page.rect.width
            height = page.rect.height
            blocks = []
            
            # Generate thumbnail for UI
            pix_thumb = page.get_pixmap(dpi=36)
            thumb_name = f"thumb_{task_id}_p{page_idx+1}.jpg"
            thumb_path = os.path.join(img_dir, thumb_name)
            pix_thumb.save(thumb_path)
            
            TASKS_DB[task_id]["progress"] = 40 + int((page_idx / max(pages_to_process, 1)) * 50)
            
            text_blocks = page.get_text("blocks")
            text_only_blocks = [b for b in text_blocks if b[6] == 0 and b[4].strip()]
            has_text = len(text_only_blocks) > 0
            has_images = len(page.get_images(full=True)) > 0
            
            if not has_text:
                pix = page.get_pixmap(dpi=150)
                img_data = pix.tobytes("png")
                img_name = f"page_{task_id}_p{page_idx+1}.png"
                img_path = os.path.join(img_dir, img_name)
                with open(img_path, "wb") as f_img:
                    f_img.write(img_data)
                
                blocks.append({
                    "blockId": f"b-pageimg-{page_idx+1}",
                    "type": "image",
                    "boundingBox": [0, 0, int(width), int(height)],
                    "imageUrl": f"/static/extracted_images/{img_name}"
                })
                
                # Only perform OCR on the first MAX_OCR_PAGES to save time
                if page_idx < MAX_OCR_PAGES:
                    try:
                        ocr_blocks = ocr_manager.process_image(img_data, filename)
                        for ob in ocr_blocks:
                            ob["type"] = "text"
                            blocks.append(ob)
                    except Exception as ocr_err:
                        logger.error(f"Page {page_idx+1} OCR error: {ocr_err}")
                else:
                    # For pages beyond the limit, add a dummy text block or just leave as image
                    pass
            else:
                for b_idx, b in enumerate(text_only_blocks):
                    x0, y0, x1, y1, text_content, block_no, block_type = b
                    blocks.append({
                        "blockId": f"b-{page_idx+1}-{b_idx+1}",
                        "type": "text",
                        "boundingBox": [int(x0), int(y0), int(x1), int(y1)],
                        "text": text_content.strip()
                    })
            
            if has_images:
                image_list = page.get_images(full=True)
                for img_idx, img_info in enumerate(image_list):
                    xref = img_info[0]
                    try:
                        base_image = pdf_doc.extract_image(xref)
                        img_bytes = base_image["image"]
                        img_ext = base_image["ext"]
                        img_w = base_image.get("width", 200)
                        img_h = base_image.get("height", 200)
                        
                        if img_w < 50 or img_h < 50:
                            continue
                        
                        img_name = f"img_{task_id}_p{page_idx+1}_{img_idx+1}.{img_ext}"
                        img_path = os.path.join(img_dir, img_name)
                        with open(img_path, "wb") as f_img:
                            f_img.write(img_bytes)
                            
                        img_text = ""
                        valid_ocr_blocks = []
                        try:
                            img_ocr_blocks = ocr_manager.process_image(img_bytes, filename)
                            # Filter out mojibake
                            for b in img_ocr_blocks:
                                t = b.get("text", "")
                                if '\ufffd' not in t and t.strip():
                                    valid_ocr_blocks.append(b)
                                    
                            img_text = " ".join([b["text"] for b in valid_ocr_blocks]).strip()
                        except Exception as ocr_err:
                            logger.error(f"Image OCR error: {ocr_err}")
                            
                        blocks.append({
                            "blockId": f"b-img-{page_idx+1}-{img_idx+1}",
                            "type": "image",
                            # Use image native size if it is a full page scan
                            "boundingBox": [0, 0, img_w, img_h],
                            "imageUrl": f"/static/extracted_images/{img_name}",
                            "rawText": img_text if img_text else "",
                            "ocrBlocks": valid_ocr_blocks
                        })
                    except Exception as ex:
                        logger.error(f"Failed to extract image xref={xref}: {ex}")
                        
            pages.append({
                "pageNumber": page_idx + 1,
                "width": int(width),
                "height": int(height),
                "blocks": blocks
            })
            
        paragraphs = []
        p_count = 1
        for page in pages:
            for b in page["blocks"]:
                if b["type"] == "text":
                    paragraphs.append({
                        "id": f"p{p_count}",
                        "rawText": b["text"],
                        "correctedText": b["text"],
                        "hasError": False,
                        "boundingBox": b["boundingBox"],
                        "pageNumber": page["pageNumber"]
                    })
                    p_count += 1
                elif b["type"] == "image":
                    paragraphs.append({
                        "id": f"p{p_count}",
                        "type": "image",
                        "imageUrl": b["imageUrl"],
                        "boundingBox": b["boundingBox"],
                        "pageNumber": page["pageNumber"],
                        "rawText": b.get("rawText", ""),
                        "ocrBlocks": b.get("ocrBlocks", [])
                    })
                    p_count += 1
                    
        TASKS_DB[task_id]["result"] = {
            "fileName": filename,
            "paragraphs": paragraphs,
            "errors": []
        }
        TASKS_DB[task_id]["progress"] = 100
        TASKS_DB[task_id]["status"] = "success"
        
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        TASKS_DB[task_id]["status"] = "error"
        TASKS_DB[task_id]["error_detail"] = "抱歉！我們無法順利讀取這份文件的內容。可能是檔案受到密碼保護，或是包含了目前尚不支援的特殊格式。"

@app.post("/api/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())
    filename = file.filename
    file_bytes = await file.read()
    
    FILE_CACHE[task_id] = {
        "filename": filename,
        "bytes": file_bytes
    }
    
    file_path = os.path.join("./data/uploads", f"{task_id}_{filename}")
    with open(file_path, "wb") as f:
        f.write(file_bytes)
        
    TASKS_DB[task_id] = {
        "status": "processing",
        "progress": 0,
        "result": None,
        "error_detail": ""
    }
    
    background_tasks.add_task(process_file_background, task_id, file_path, filename)
    return {"status": "success", "taskId": task_id, "fileName": filename}

@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    if task_id not in TASKS_DB:
        raise HTTPException(status_code=404, detail="Task not found")
    return TASKS_DB[task_id]

@app.post("/api/export/docx")
async def export_docx(payload: PdfExportRequest):
    file_id = payload.fileId
    if file_id in FILE_CACHE:
        original_bytes = FILE_CACHE[file_id]["bytes"]
        filename = FILE_CACHE[file_id]["filename"]
    else:
        original_bytes = b""
        filename = "mock.pdf"
        
    corrs_list = payload.corrections
    try:
        exported_bytes = export_to_docx(original_bytes, payload.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    encoded_filename = urllib.parse.quote(f"{filename.split('.')[0]}_corrected.docx")
    headers = {
        "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
        "Access-Control-Expose-Headers": "Content-Disposition"
    }
    return StreamingResponse(
        io.BytesIO(exported_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers
    )

@app.post("/api/export/pdf")
async def export_pdf(payload: PdfExportRequest):
    file_id = payload.fileId
    if file_id in FILE_CACHE:
        original_bytes = FILE_CACHE[file_id]["bytes"]
        filename = FILE_CACHE[file_id]["filename"]
    else:
        original_bytes = b""
        filename = "mock.pdf"
        
    corrs_list = payload.corrections
    try:
        exported_bytes = export_to_pdf(original_bytes, payload.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    encoded_filename = urllib.parse.quote(f"{filename.split('.')[0]}_corrected.pdf")
    headers = {
        "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
        "Access-Control-Expose-Headers": "Content-Disposition"
    }
    return StreamingResponse(
        io.BytesIO(exported_bytes),
        media_type="application/pdf",
        headers=headers
    )

@app.delete("/api/file/{file_id}")
async def delete_file(file_id: str):
    if file_id in FILE_CACHE:
        del FILE_CACHE[file_id]
    if file_id in TASKS_DB:
        del TASKS_DB[file_id]
    return {"status": "success", "message": f"File and task {file_id} deleted."}

@app.post("/api/redetect")
async def redetect_errors(request: Request, payload: RedetectRequest):
    global RATE_LIMIT_DATE, IP_RATE_LIMIT
    today = datetime.date.today()
    if today != RATE_LIMIT_DATE:
        RATE_LIMIT_DATE = today
        IP_RATE_LIMIT = {}
        
    client_ip = request.client.host
    if IP_RATE_LIMIT.get(client_ip, 0) >= 5:
        raise HTTPException(status_code=429, detail="本日偵測額度已用盡（每日限制 5 次）。請等待隔天 0 點後再試。")
        
    IP_RATE_LIMIT[client_ip] = IP_RATE_LIMIT.get(client_ip, 0) + 1

    errors = []
    base_prompt = "你是一個精通台灣繁體中文的嚴格校對助理。\n我將給你一份由多個段落組成的文件，每個段落都有其專屬的 paragraphId。\n請特別注意，有些詞彙可能因為版面空間而跨段落斷行（例如上一段結尾是『本』，下一段開頭是『質』，合起來是『本質』），這屬於正常的排版斷行，請將相鄰的段落視為上下文一起閱讀，絕對不可因為跨行斷字而將其誤判為錯字或語意不順。\n"
    
    if payload.mode == "typo":
        task_prompt = "請發揮最嚴格的標準，無情地找出所有的「錯別字」（包含同音異字、形近字、故意測試打錯的字，如把系統打成「細統」）。請完全專注於尋找錯字，忽略語意不順或標點符號問題。"
    elif payload.mode == "grammar":
        task_prompt = "請發揮最嚴格的標準，專注尋找「語意不通順、贅字」以及「標點符號錯用」的問題。請忽略單純的錯別字，純粹針對語句通順度進行優化與建議。"
    else:
        task_prompt = "請發揮最嚴格的標準，無情地找出所有的錯別字（包含同音異字、形近字）、語意不通順的贅字，以及標點符號的錯用。"
        
    format_prompt = """
請回傳一個 JSON 陣列，裡面的每個物件必須包含以下欄位：
- "wrongWord": 原文中的錯字或錯誤片段 (請精準對應原文，以利程式替換)
- "suggestedWord": 您建議修改後的字詞
- "reason": 解釋為何這樣改比較好

絕對不要回傳 Markdown 標記，純粹回傳乾淨的 JSON 陣列，例如：
[{"wrongWord": "細統", "suggestedWord": "系統", "reason": "錯別字"}]
如果整份文件都沒有錯誤，請回傳 []"""

    system_prompt = base_prompt + task_prompt + "\n" + format_prompt
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise HTTPException(status_code=500, detail="AI 系統尚未綁定金鑰，請確認已設定正確的 GEMINI_API_KEY。")
        
    err_count = 1

    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Build concatenated text and char mapping
        full_text = ""
        char_mapping = []
        for p in payload.paragraphs:
            text = p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
            p_id = p.get("id") if isinstance(p, dict) else getattr(p, "id")
            if text and str(text).strip():
                start_idx = len(full_text)
                full_text += text
                end_idx = len(full_text)
                char_mapping.append({
                    "id": p_id,
                    "start": start_idx,
                    "end": end_idx,
                    "text": text
                })
                
        if not full_text:
            return {"status": "success", "errors": []}
            
        prompt = f"{system_prompt}\\n\\n[待校對文章開始]\\n{full_text}\\n[待校對文章結束]\\n\\n請以 JSON 回傳您的偵測結果："
        response = model.generate_content(prompt)
        
        try:
            import json
            res_text = response.text.strip()
            if res_text.startswith("```json"):
                res_text = res_text[7:]
            elif res_text.startswith("```"):
                res_text = res_text[3:]
            if res_text.endswith("```"):
                res_text = res_text[:-3]
                
            llm_errors = json.loads(res_text.strip())
            
            for err in llm_errors:
                wrong = err.get("wrongWord", "")
                suggested = err.get("suggestedWord", "")
                reason = err.get("reason", "")
                
                if not wrong:
                    continue
                    
                # Find all occurrences in full_text
                start_idx = full_text.find(wrong)
                found_pids = set()
                while start_idx != -1:
                    for mapping in char_mapping:
                        if mapping["start"] <= start_idx < mapping["end"]:
                            found_pids.add(mapping["id"])
                            break
                    start_idx = full_text.find(wrong, start_idx + 1)
                    
                for p_id in found_pids:
                    errors.append({
                        "id": f"e-llm-{err_count}-{uuid.uuid4().hex[:4]}",
                        "paragraphId": p_id,
                        "wrongWord": wrong,
                        "suggestedWord": suggested,
                        "reason": reason,
                        "startIndex": 0,
                        "endIndex": len(wrong),
                        "boundingBox": [0, 0, 0, 0],
                        "pageNumber": 1
                    })
                    err_count += 1

        except Exception as parse_err:
            logger.error(f"Failed to parse LLM response: {parse_err}\\nRaw: {response.text}")
            raise HTTPException(status_code=500, detail="AI 剛剛恍神了，沒有回傳我們預期的格式。請您再點擊一次重新偵測。")
        
        return {"status": "success", "errors": errors, "remainingLimit": 5 - IP_RATE_LIMIT.get(client_ip, 0)}

    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        raise HTTPException(status_code=500, detail="AI 服務暫時無法連線。可能是網路不穩，或是 AI 模型目前較為忙碌，請您稍後再試一次。")