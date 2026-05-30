import io
import logging
import docx
import fitz

logger = logging.getLogger("export_service")

def replace_word_in_runs(paragraph, wrong_word: str, suggested_word: str) -> bool:
    """
    在 python-docx 的段落 runs 中替換錯字，保留原始的 runs 格式與樣式設定。
    """
    # 1. 優先匹配：錯字完整被包含在單一 run.text 中
    replaced = False
    for run in paragraph.runs:
        if wrong_word in run.text:
            run.text = run.text.replace(wrong_word, suggested_word)
            replaced = True
            
    if replaced:
        return True

    # 2. 備用匹配：錯字跨多個 runs (跨段落字元拼接)
    run_texts = [run.text for run in paragraph.runs]
    full_text = "".join(run_texts)
    
    if wrong_word in full_text:
        start_pos = full_text.find(wrong_word)
        end_pos = start_pos + len(wrong_word)
        
        curr_len = 0
        start_run_idx = -1
        end_run_idx = -1
        
        # 尋找錯字橫跨的 runs 範圍
        for idx, run in enumerate(paragraph.runs):
            next_len = curr_len + len(run.text)
            if curr_len <= start_pos < next_len:
                start_run_idx = idx
            if curr_len < end_pos <= next_len:
                end_run_idx = idx
                break
            curr_len = next_len
            
        if start_run_idx != -1 and end_run_idx != -1:
            # 提取並合併跨 runs 範圍的完整文字
            combined = ""
            for r_idx in range(start_run_idx, end_run_idx + 1):
                combined += paragraph.runs[r_idx].text
            
            # 進行文字替換
            new_combined = combined.replace(wrong_word, suggested_word)
            
            # 將新文字覆寫回第一個 run，其餘橫跨的 runs 清空
            paragraph.runs[start_run_idx].text = new_combined
            for r_idx in range(start_run_idx + 1, end_run_idx + 1):
                paragraph.runs[r_idx].text = ""
            return True
            
    return False

def export_to_docx(original_bytes: bytes, payload: dict) -> bytes:
    """
    全面匯出為 Word (DOCX)。
    不再讀取原始 PDF，直接將所有段落重新寫入全新的 Word 檔案，統一字體與排版。
    """
    paragraphs = payload.get("paragraphs", [])
    logger.info("Generating a completely new DOCX document from paragraphs...")
    
    doc = docx.Document()
    
    # 設置統一字體與排版
    style = doc.styles['Normal']
    from docx.shared import Pt
    style.font.name = 'Microsoft JhengHei'
    style.font.size = Pt(12)
    try:
        from docx.oxml.ns import qn
        style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft JhengHei')
    except Exception:
        pass

    for p in paragraphs:
        if not p.get("type") or p.get("type") == "text":
            text = p.get("correctedText", p.get("rawText", ""))
            if str(text).strip():
                doc.add_paragraph(text)
                
    # 將修改後的 DOCX 轉為 Bytes 流
    out_io = io.BytesIO()
    doc.save(out_io)
    return out_io.getvalue()

def export_to_pdf(original_bytes: bytes, payload: dict) -> bytes:
    """
    後端 PDF 座標還原匯出。
    對於原生文字：使用 search_for 尋找錯字並覆蓋。
    對於掃描圖片：使用 ocrBlocks 計算文字座標，等比例縮放後覆蓋。
    """
    corrections = payload.get("corrections", [])
    paragraphs = payload.get("paragraphs", [])
    logger.info("Starting PDF export layout coordinates preservation...")
    
    try:
        pdf_doc = fitz.open(stream=original_bytes, filetype="pdf")
        
        para_map = {p["id"]: p for p in paragraphs}
        
        for corr in corrections:
            para_id = corr.get("paragraphId", "")
            wrong_word = corr.get("wrongWord", "")
            suggested_word = corr.get("suggestedWord", "")
            
            if not wrong_word or not para_id:
                continue
                
            p_data = para_map.get(para_id)
            if not p_data:
                continue
                
            page_idx = p_data.get("pageNumber", 1) - 1
            if not (0 <= page_idx < len(pdf_doc)):
                continue
                
            page = pdf_doc[page_idx]
            
            if p_data.get("type") == "image":
                ocr_blocks = p_data.get("ocrBlocks", [])
                img_bbox = p_data.get("boundingBox", [0,0,1,1])
                img_w = max(1, img_bbox[2] - img_bbox[0])
                img_h = max(1, img_bbox[3] - img_bbox[1])
                
                scale_x = page.rect.width / img_w
                scale_y = page.rect.height / img_h
                
                for b in ocr_blocks:
                    b_text = b.get("text", "")
                    start_idx = b_text.find(wrong_word)
                    if start_idx != -1:
                        b_box = b.get("boundingBox", [0,0,0,0])
                        
                        bx0 = b_box[0] * scale_x
                        by0 = b_box[1] * scale_y
                        bx1 = b_box[2] * scale_x
                        by1 = b_box[3] * scale_y
                        
                        b_width = bx1 - bx0
                        char_w = b_width / max(1, len(b_text))
                        
                        wx0 = bx0 + start_idx * char_w
                        wx1 = wx0 + len(wrong_word) * char_w
                        word_rect = fitz.Rect(wx0, by0, wx1, by1)
                        
                        # Draw white patch (dog-plaster)
                        page.draw_rect(word_rect, color=(1, 1, 1), fill=(1, 1, 1), width=0)
                        
                        font_size = max(6, int((by1 - by0) * 0.8))
                        page.insert_text(fitz.Point(wx0, by1 - (by1-by0)*0.15), suggested_word, fontname="china-t", fontsize=font_size, color=(0.12, 0.16, 0.22))
            else:
                rects = page.search_for(wrong_word)
                for rect in rects:
                    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), width=0)
                    box_w = rect.width
                    box_h = rect.height
                    font_size = max(8, int(box_h * 0.82))
                    text_len = fitz.get_text_length(text=suggested_word, fontname="china-t", fontsize=font_size)
                    if text_len > box_w and text_len > 0:
                        font_size = int(font_size * (box_w / text_len) * 0.95)
                        font_size = max(6, font_size)
                        
                    point = fitz.Point(rect.x0, rect.y1 - (box_h * 0.15))
                    page.insert_text(point, suggested_word, fontname="china-t", fontsize=font_size, color=(0.12, 0.16, 0.22))
                    
    except Exception as e:
        logger.warning(f"Failed to parse original PDF. Generating a new one from paragraphs. {e}")
        pdf_doc = fitz.open()
        page = pdf_doc.new_page()
        y_pos = 50
        for p in paragraphs:
            text = p.get("correctedText", p.get("rawText", ""))
            if str(text).strip():
                for line in [text[i:i+40] for i in range(0, len(text), 40)]:
                    page.insert_text(fitz.Point(50, y_pos), line, fontname="china-t", fontsize=12, color=(0,0,0))
                    y_pos += 20
                    if y_pos > 750:
                        page = pdf_doc.new_page()
                        y_pos = 50
                        
    pdf_bytes = pdf_doc.write()
    pdf_doc.close()
    return pdf_bytes
