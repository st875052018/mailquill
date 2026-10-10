"""「商家名稱整欄是圖片」型電子帳單的 OCR 抽字：HSBC 首先遇到這種版型
（無 ToUnicode 對映的內嵌小圖），後來中信（CTBC）PDF 也是同一套做法，
故用 `OcrLayout` 把「商家小圖的欄位座標／明細列錨定樣式」參數化，兩家
共用同一條逐列裁圖→OCR→接回原文字列的流程。模組名沿用 hsbc_ 只是歷史因素。

版型觀察（`pdfplumber`）：
- 日期／金額／固定文字標籤都是可直接抽取的真文字（`page.extract_words`）。
- 每個消費/手續費明細列的商家名稱是一張獨立內嵌小圖，位置固定落在
  x0 100~145pt、高 10~15pt 的欄位（同一欄同時也放固定標籤如「前期餘額」，
  但那些列不會被 `_DETAIL_ANCHOR` 比對到，不會觸發 OCR，省時間）。
- 圖片的 top/bottom 涵蓋對應文字列的 top（同一視覺列），可用來配對。

商家名稱來自 OCR，非 100% 準確——尤其海外消費列商家欄常混入國旗小圖示，
被誤判成雜訊字元（如 "APE7844" 之類前綴）。這是已知限制：不在此做啟發式
清洗（清不乾淨還可能誤刪真商家名），交給分類規則或手動修正。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pdfplumber

try:
    import pytesseract
except ImportError:  # pragma: no cover — 未裝 pytesseract 時延後到呼叫才報錯
    pytesseract = None

_OCR_LANG = "chi_tra+eng"


@dataclass(frozen=True)
class OcrLayout:
    """某家銀行 PDF 裡「商家小圖」落在哪一欄、哪些列是明細列。"""
    merchant_x0_range: tuple[float, float]
    merchant_height_range: tuple[float, float]
    detail_anchor: re.Pattern


HSBC_LAYOUT = OcrLayout(
    merchant_x0_range=(100, 145),
    merchant_height_range=(10, 15),
    detail_anchor=re.compile(r"^\d{2}/\d{2}\s+\d{2}/\d{2}\b"),
)
# CTBC：明細列以兩個完整民國日期開頭；商家小圖 x0≈125pt、高約 8~9pt，
# 每列一張（eTag 這類本來就有真文字商家的列也會有一張，parser 端自行取捨）。
CTBC_LAYOUT = OcrLayout(
    merchant_x0_range=(115, 150),
    merchant_height_range=(6, 12),
    detail_anchor=re.compile(r"^\d{3}/\d{2}/\d{2}\s+\d{3}/\d{2}/\d{2}\b"),
)
_OCR_CONFIG = "--psm 7"


def _line_groups(page) -> list[tuple[float, float, str]]:
    """把 `extract_words` 依視覺列（top 相近）分組、依 x0 排序後接成一行文字。"""
    words = page.extract_words()
    lines: dict[int, list[dict]] = {}
    for w in words:
        lines.setdefault(round(w["top"]), []).append(w)
    groups = []
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in ws)
        top = min(w["top"] for w in ws)
        bottom = max(w["bottom"] for w in ws)
        groups.append((top, bottom, text))
    merged: list[tuple[float, float, str]] = []
    for top, bottom, text in groups:
        if merged and top - merged[-1][1] < 3:
            ptop, pbottom, ptext = merged[-1]
            merged[-1] = (min(ptop, top), max(pbottom, bottom), f"{ptext} {text}".strip())
            continue
        merged.append((top, bottom, text))
    return merged


def _merchant_images(page, layout: OcrLayout) -> list[dict]:
    x0_lo, x0_hi = layout.merchant_x0_range
    h_lo, h_hi = layout.merchant_height_range
    imgs = [
        im for im in page.images
        if x0_lo <= im["x0"] <= x0_hi and h_lo <= im["height"] <= h_hi
    ]
    return sorted(imgs, key=lambda im: im["top"])


def _ocr_crop(page_image, im: dict, resolution: int) -> str:
    scale = resolution / 72
    box = (
        int(im["x0"] * scale) - 2, int(im["top"] * scale) - 2,
        int(im["x1"] * scale) + 2, int(im["bottom"] * scale) + 2,
    )
    crop = page_image.original.crop(box)
    return pytesseract.image_to_string(crop, lang=_OCR_LANG, config=_OCR_CONFIG).strip()


def extract_pdf_text_ocr(pdf_bytes: bytes, resolution: int = 400,
                         layout: OcrLayout = HSBC_LAYOUT) -> str:
    """抽字並把明細列的商家小圖 OCR 結果接在該列文字後面回傳（跨頁以換行合併）。"""
    if pytesseract is None:
        raise RuntimeError("需要 pytesseract 才能解析圖片型商家名稱的帳單"
                           "（brew install tesseract tesseract-lang）")

    out_pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            imgs = _merchant_images(page, layout)
            page_image = None
            out_lines = []
            for top, bottom, text in _line_groups(page):
                if layout.detail_anchor.match(text):
                    match_im = next(
                        (im for im in imgs if im["top"] - 1 <= top <= im["bottom"] + 1), None,
                    )
                    if match_im:
                        if page_image is None:
                            page_image = page.to_image(resolution=resolution)
                        merchant = _ocr_crop(page_image, match_im, resolution)
                        if merchant:
                            text = f"{text} {merchant}"
                out_lines.append(text)
            out_pages.append("\n".join(out_lines))
    return "\n".join(out_pages)
