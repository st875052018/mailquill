"""hsbc_ocr_text 測試：合成 PDF（真文字列 + 內嵌小圖當「商家名稱」），
驗證商家欄位圖片配對＋OCR 真的會跑並接回原文字列。兩種 OCR 引擎各跑一遍：
macOS Vision（ocrmac）與 tesseract（含 chi_tra）；本機沒有的引擎就跳過，
不讓 CI 因缺系統套件而炸。
"""
import io

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import mailquill.hsbc_ocr_text as mod
from mailquill.hsbc_ocr_text import extract_pdf_text_ocr


def _tesseract_ready() -> bool:
    if mod.pytesseract is None:
        return False
    try:
        mod.pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


@pytest.fixture(autouse=True, params=["vision", "tesseract"])
def engine(request, monkeypatch):
    if request.param == "vision" and mod.ocrmac is None:
        pytest.skip("ocrmac 未安裝（非 macOS），跳過 Vision OCR 測試")
    if request.param == "tesseract" and not _tesseract_ready():
        pytest.skip("tesseract 未安裝，跳過 tesseract OCR 測試")
    monkeypatch.setenv("MAILQUILL_OCR", request.param)
    return request.param


def _merchant_image(text: str) -> Image.Image:
    img = Image.new("RGB", (500, 150), "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), text, fill="black", font=ImageFont.load_default(size=80))
    return img


def _pdf_with_detail_row(merchant_text: str | None) -> bytes:
    """畫一列「07/19 07/22 JPN JPY 2,112.00 07/19 422」真文字，
    在商家欄位座標 (x0 100~145pt、高 10~15pt) 疊一張商家名稱小圖（若有給）。
    """
    buf = io.BytesIO()
    page_height = 792
    c = canvas.Canvas(buf, pagesize=(612, page_height))
    c.setFont("Helvetica", 10)
    y_baseline = 700
    c.drawString(72, y_baseline, "07/19 07/22 JPN JPY 2,112.00 07/19 422")
    if merchant_text is not None:
        img = _merchant_image(merchant_text)
        c.drawImage(ImageReader(img), 120, 698, width=40, height=14, mask="auto")
    # 一列不會被 _DETAIL_ANCHOR 比對到的固定標籤，同一欄位也疊一張圖：
    # 用來確認非明細列不會被觸發 OCR。(reportlab 內建字型沒有中文字型，
    # 這裡用英文標籤純粹是避開字型問題，跟中文與否無關。)
    c.drawString(72, 650, "PREV BALANCE")
    label_img = _merchant_image("SHOULD NOT OCR")
    c.drawImage(ImageReader(label_img), 120, 648, width=60, height=14, mask="auto")
    c.save()
    return buf.getvalue()


def test_ocr_splices_merchant_into_detail_line():
    pdf = _pdf_with_detail_row("TEST SHOP")
    text = extract_pdf_text_ocr(pdf)
    lines = [l for l in text.splitlines() if l.strip()]
    detail_lines = [l for l in lines if l.startswith("07/19 07/22")]
    assert len(detail_lines) == 1
    # OCR 非 100% 乾淨（可能混入邊緣雜訊字元），只驗證商家文字有接上去。
    assert detail_lines[0].startswith("07/19 07/22 JPN JPY 2,112.00 07/19 422 ")
    assert "TEST SHOP" in detail_lines[0]


def test_ocr_leaves_non_detail_lines_untouched():
    pdf = _pdf_with_detail_row("TEST SHOP")
    text = extract_pdf_text_ocr(pdf)
    label_lines = [l for l in text.splitlines() if "PREV BALANCE" in l]
    assert label_lines == ["PREV BALANCE"]     # 沒被誤接商家圖 OCR 結果


def test_ocr_missing_merchant_image_leaves_line_as_is():
    pdf = _pdf_with_detail_row(None)
    text = extract_pdf_text_ocr(pdf)
    lines = [l for l in text.splitlines() if l.startswith("07/19 07/22")]
    assert lines == ["07/19 07/22 JPN JPY 2,112.00 07/19 422"]


def test_ocr_raises_when_chosen_engine_missing(engine, monkeypatch):
    monkeypatch.setattr(mod, "ocrmac" if engine == "vision" else "pytesseract", None)
    with pytest.raises(RuntimeError):
        mod.extract_pdf_text_ocr(_pdf_with_detail_row("TEST SHOP"))


def test_default_engine_prefers_vision_then_tesseract(monkeypatch):
    monkeypatch.delenv("MAILQUILL_OCR", raising=False)
    monkeypatch.setattr(mod, "ocrmac", object())
    assert mod.ocr_engine() == "vision"
    monkeypatch.setattr(mod, "ocrmac", None)
    assert mod.ocr_engine() == "tesseract"


def test_unknown_engine_name_raises(monkeypatch):
    monkeypatch.setenv("MAILQUILL_OCR", "abbyy")
    with pytest.raises(ValueError):
        mod.ocr_engine()
