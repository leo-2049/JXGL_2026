"""PDF 生成：受控水印、单据打印版式（含手写签名与电子章）。"""
import base64
import io
from typing import Iterable

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
COMPANY = "上海顺翼紧固件有限公司"


def _watermark_page(text: str, w: float, h: float, color=colors.red) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFont(FONT, 28)
    c.setFillColor(color, alpha=0.18)
    c.saveState()
    c.translate(w / 2, h / 2)
    c.rotate(35)
    for dy in (-180, 0, 180):
        c.drawCentredString(0, dy, text)
    c.restoreState()
    c.setFont(FONT, 9)
    c.setFillColor(color, alpha=0.9)
    c.drawRightString(w - 20, h - 18, text)
    c.save()
    buf.seek(0)
    return PdfReader(buf)


def watermark(src: bytes | None, text: str, title: str = "", obsolete: bool = False) -> bytes:
    """给 PDF 每页加水印；原文件不是 PDF 时生成一页封面。"""
    color = colors.grey if obsolete else colors.red
    writer = PdfWriter()
    reader = None
    if src and src[:4] == b"%PDF":
        try:
            reader = PdfReader(io.BytesIO(src))
        except Exception:  # noqa: BLE001 - 损坏的 PDF 走封面
            reader = None
    if reader is None:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setFont(FONT, 18)
        c.drawCentredString(A4[0] / 2, A4[1] - 120, COMPANY)
        c.setFont(FONT, 16)
        c.drawCentredString(A4[0] / 2, A4[1] - 160, title)
        c.setFont(FONT, 11)
        c.drawCentredString(A4[0] / 2, A4[1] - 190, "（原文件为可编辑格式，受控版本以本封面 + 原文件为准）")
        c.save()
        buf.seek(0)
        reader = PdfReader(buf)
    for page in reader.pages:
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        page.merge_page(_watermark_page(text, w, h, color).pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _img(data_url: str | None, w=30 * mm, h=12 * mm):
    if not data_url or "," not in data_url:
        return ""
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
        return Image(io.BytesIO(raw), width=w, height=h, kind="proportional")
    except Exception:  # noqa: BLE001
        return ""


def form_pdf(title: str, form_no: str, header: list[tuple[str, str]], tables: Iterable[dict],
             signatures: list[tuple[str, str | None, str]] = (), stamp: str | None = None, land: bool = False,
             footer: str = "") -> bytes:
    """通用单据版式：表头键值网格 + 若干明细表 + 签名区 + 电子章（只在最终节点通过后传入 stamp）。

    tables: [{"title":..., "columns":[...], "rows":[[...]], "col_widths":[...]?}]
    signatures: [(角色, 签名图片 data_url, 姓名 日期)]
    """
    buf = io.BytesIO()
    size = landscape(A4) if land else A4
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=title)
    st = ParagraphStyle("cn", fontName=FONT, fontSize=8.5, leading=11)
    st_h = ParagraphStyle("h", fontName=FONT, fontSize=14, leading=18, alignment=1)
    st_c = ParagraphStyle("c", fontName=FONT, fontSize=9, leading=12, alignment=1)
    P = lambda x: Paragraph(str("" if x is None else x).replace("\n", "<br/>"), st)  # noqa: E731
    width = size[0] - 24 * mm
    els = [Paragraph(COMPANY, st_c), Paragraph(title, st_h), Spacer(1, 4)]
    cells, row = [], []
    for k, v in header:
        row += [P(f"<b>{k}</b>"), P(v)]
        if len(row) == 6:
            cells.append(row)
            row = []
    if row:
        cells.append(row + [""] * (6 - len(row)))
    if cells:
        t = Table(cells, colWidths=[width / 6 * 0.7, width / 6 * 1.3] * 3)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                               ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                               ("BACKGROUND", (2, 0), (2, -1), colors.whitesmoke),
                               ("BACKGROUND", (4, 0), (4, -1), colors.whitesmoke),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        els.append(t)
    for tb in tables:
        els += [Spacer(1, 6), Paragraph(f"<b>{tb['title']}</b>", st)]
        data = [[P(f"<b>{c}</b>") for c in tb["columns"]]] + [[P(c) for c in r] for r in tb["rows"]]
        if len(data) == 1:
            data.append([P("—")] + [P("") for _ in tb["columns"][1:]])
        n = len(tb["columns"])
        cw = tb.get("col_widths") or [width / n] * n
        t = Table(data, colWidths=cw, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        els.append(t)
    if signatures:
        els.append(Spacer(1, 8))
        sig_row = []
        for role, img, who in signatures:
            sig_row.append([P(f"<b>{role}</b>"), _img(img) or P(""), P(who)])
        t = Table([[x for trio in sig_row for x in trio]],
                  colWidths=[width / (3 * len(sig_row))] * (3 * len(sig_row)))
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        els.append(t)
    els.append(Spacer(1, 6))
    els.append(Paragraph(f"表单编号：{form_no}　{footer}", st))

    def on_page(c, _doc):
        if stamp:
            draw_stamp(c, stamp, size[0] - 62 * mm, 16 * mm)

    doc.build(els, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def draw_stamp(c, stamp: str, x: float, y: float, d: float = 40 * mm):
    """电子章：上传的实章图片（data:image/...）或矢量章「上方文字|中间文字」。只在最终节点通过后由系统加盖。"""
    if stamp.startswith("data:image"):
        try:
            raw = base64.b64decode(stamp.split(",", 1)[1])
            c.drawImage(ImageReader(io.BytesIO(raw)), x, y, d, d, mask="auto")
        except Exception:  # noqa: BLE001
            pass
        return
    top, _, mid = stamp.partition("|")
    c.saveState()
    c.setStrokeColor(colors.red)
    c.setFillColor(colors.red)
    c.setLineWidth(2)
    r = d / 2
    c.circle(x + r, y + r, r - 2)
    c.setFont(FONT, 7.5)
    c.drawCentredString(x + r, y + r + 6, top)
    c.setFont(FONT, 11)
    c.drawCentredString(x + r, y + r - 8, mid or "")
    c.drawCentredString(x + r, y + r - 1, "★")
    c.restoreState()


STAMPS = {"QA": f"{COMPANY}|质量合格章", "LAB": f"{COMPANY}检测中心|检测专用章"}
