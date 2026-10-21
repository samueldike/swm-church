"""Render a Books Pub document to .docx and .pdf."""
from __future__ import annotations

import io
import re
import unicodedata
from xml.sax.saxutils import escape

from app.core.publication_html import Block, Run, parse_blocks

SERIF_DEFAULT = "Times New Roman"
_SANS = ("arial", "helvetica", "calibri", "verdana", "tahoma", "segoe", "trebuchet", "open sans", "roboto", "sans")
_MONO = ("courier", "consolas", "monaco", "mono")


def _hex(color: str | None) -> str | None:
    """Best-effort '#rrggbb' from an editor colour value."""
    if not color:
        return None
    c = color.strip().lower()
    m = re.match(r"^#([0-9a-f]{6})$", c)
    if m:
        return m.group(1)
    m = re.match(r"^#([0-9a-f])([0-9a-f])([0-9a-f])$", c)
    if m:
        return "".join(ch * 2 for ch in m.groups())
    m = re.match(r"^rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)", c)
    if m:
        return "".join(f"{min(int(v), 255):02x}" for v in m.groups())
    named = {"black": "000000", "white": "ffffff", "red": "ff0000", "blue": "0000ff", "green": "008000",
             "gray": "808080", "grey": "808080", "orange": "ffa500", "purple": "800080"}
    return named.get(c)


# ─── DOCX ────────────────────────────────────────────────────────────

def build_docx(title: str, doc_html: str, author: str | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    d = Document()
    section = d.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.5)
    section.top_margin = section.bottom_margin = Cm(2.5)

    normal = d.styles["Normal"]
    normal.font.name = SERIF_DEFAULT
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SERIF_DEFAULT)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.25
    sizes = {"h1": 22, "h2": 17, "h3": 14, "h4": 12}
    for key, size in sizes.items():
        st = d.styles[f"Heading {key[1]}"]
        st.font.name = SERIF_DEFAULT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), SERIF_DEFAULT)
        st.element.rPr.rFonts.set(qn("w:ascii"), SERIF_DEFAULT)
        st.element.rPr.rFonts.set(qn("w:hAnsi"), SERIF_DEFAULT)

    d.core_properties.title = title
    if author:
        d.core_properties.author = author

    # Footer page number
    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)
    run.font.size = Pt(10)

    aligns = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
              "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT}

    def add_runs(par, runs: list[Run]) -> None:
        for r in runs:
            if r.text == "\n":
                par.add_run().add_break(WD_BREAK.LINE)
                continue
            if r.link:
                _docx_hyperlink(par, r)
                continue
            run = par.add_run(r.text)
            _docx_fmt(run, r)

    def _docx_fmt(run, r: Run) -> None:
        run.bold = r.bold or None
        run.italic = r.italic or None
        run.underline = r.underline or None
        run.font.strike = r.strike or None
        run.font.superscript = r.sup or None
        run.font.subscript = r.sub or None
        if r.size_pt:
            run.font.size = Pt(r.size_pt)
        if r.font:
            run.font.name = r.font
            run._r.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), r.font)
        hx = _hex(r.color)
        if hx:
            run.font.color.rgb = RGBColor.from_string(hx.upper())

    def _docx_hyperlink(par, r: Run) -> None:
        part = par.part
        rid = part.relate_to(r.link, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), rid)
        new_run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        col = OxmlElement("w:color")
        col.set(qn("w:val"), "0563C1")
        rpr.append(col)
        rpr.append(u)
        new_run.append(rpr)
        t = OxmlElement("w:t")
        t.text = r.text
        t.set(qn("xml:space"), "preserve")
        new_run.append(t)
        h.append(new_run)
        par._p.append(h)

    first_break_pending = False
    blocks = parse_blocks(doc_html)
    for i, blk in enumerate(blocks):
        if blk.kind == "pagebreak":
            # Avoid an empty first page when the document opens with a break.
            if i == 0 or d.paragraphs == []:
                continue
            first_break_pending = True
            continue
        if blk.kind == "table":
            _docx_table(d, blk, add_runs, aligns)
            continue
        style = {"h1": "Heading 1", "h2": "Heading 2", "h3": "Heading 3", "h4": "Heading 4"}.get(blk.kind)
        par = d.add_paragraph(style=style) if style else d.add_paragraph()
        if first_break_pending:
            par.paragraph_format.page_break_before = True
            first_break_pending = False
        if blk.align in aligns:
            par.alignment = aligns[blk.align]
        if blk.space_before_pt:
            par.paragraph_format.space_before = Pt(blk.space_before_pt)
        if blk.kind == "quote":
            par.paragraph_format.left_indent = Cm(1.0)
        if blk.list_prefix:
            par.paragraph_format.left_indent = Cm(0.9 + 0.8 * blk.level)
            par.paragraph_format.first_line_indent = Cm(-0.6)
            par.add_run(f"{blk.list_prefix} ")
        elif blk.indent_pt:
            par.paragraph_format.left_indent = Pt(blk.indent_pt)
        add_runs(par, blk.runs)

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _docx_table(d, blk: Block, add_runs, aligns) -> None:
    rows = [r for r in blk.rows if r]
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = d.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    for ri, row in enumerate(rows):
        for ci, cell_blk in enumerate(row):
            cell = table.cell(ri, ci)
            par = cell.paragraphs[0]
            if cell_blk.align in aligns:
                par.alignment = aligns[cell_blk.align]
            add_runs(par, cell_blk.runs)
    d.add_paragraph()


# ─── PDF ─────────────────────────────────────────────────────────────

def _pdf_safe(text: str) -> str:
    """Standard PDF fonts are WinAnsi only; fold anything else to its closest cp1252 form."""
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch)
            base = "".join(c for c in base if not unicodedata.combining(c))
            out.append(base if base and all(_encodable(c) for c in base) else "?")
    return "".join(out)


def _encodable(ch: str) -> bool:
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _pdf_family(font: str | None) -> str:
    f = (font or "").lower()
    if any(k in f for k in _MONO):
        return "Courier"
    if any(k in f for k in _SANS):
        return "Helvetica"
    return "Times-Roman"


def _pdf_markup(runs: list[Run]) -> str:
    parts = []
    for r in runs:
        if r.text == "\n":
            parts.append("<br/>")
            continue
        t = escape(_pdf_safe(r.text))
        if not t:
            continue
        attrs = []
        if r.font:
            attrs.append(f'name="{_pdf_family(r.font)}"')
        if r.size_pt:
            attrs.append(f'size="{r.size_pt:.1f}"')
        hx = _hex(r.color)
        if hx:
            attrs.append(f'color="#{hx}"')
        if attrs:
            t = f"<font {' '.join(attrs)}>{t}</font>"
        if r.bold:
            t = f"<b>{t}</b>"
        if r.italic:
            t = f"<i>{t}</i>"
        if r.underline:
            t = f"<u>{t}</u>"
        if r.strike:
            t = f"<strike>{t}</strike>"
        if r.sup:
            t = f"<super>{t}</super>"
        if r.sub:
            t = f"<sub>{t}</sub>"
        if r.link:
            t = f'<a href="{escape(r.link, {chr(34): "&quot;"})}" color="#0563C1">{t}</a>'
        parts.append(t)
    return "".join(parts)


def build_pdf(title: str, doc_html: str, author: str | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 10)
        canvas.drawCentredString(A4[0] / 2, 1.3 * cm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=2.5 * cm, rightMargin=2.5 * cm, topMargin=2.5 * cm, bottomMargin=2.5 * cm,
        title=_pdf_safe(title), author=_pdf_safe(author or ""),
    )
    base = ParagraphStyle("base", fontName="Times-Roman", fontSize=12, leading=17, spaceAfter=8)
    heads = {
        "h1": ParagraphStyle("h1", parent=base, fontName="Times-Bold", fontSize=22, leading=27, spaceAfter=12),
        "h2": ParagraphStyle("h2", parent=base, fontName="Times-Bold", fontSize=17, leading=22, spaceAfter=8),
        "h3": ParagraphStyle("h3", parent=base, fontName="Times-Bold", fontSize=14, leading=19, spaceAfter=8),
        "h4": ParagraphStyle("h4", parent=base, fontName="Times-Bold", fontSize=12, leading=16, spaceAfter=6),
    }
    aligns = {"center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY, "left": TA_LEFT}

    def style_for(blk: Block) -> ParagraphStyle:
        st = ParagraphStyle(f"s{id(blk)}", parent=heads.get(blk.kind, base))
        if blk.align in aligns:
            st.alignment = aligns[blk.align]
        if blk.kind == "quote":
            st.leftIndent = 1.0 * cm
        if blk.list_prefix:
            st.leftIndent = (0.9 + 0.8 * blk.level) * cm
            st.firstLineIndent = -0.6 * cm
        elif blk.indent_pt:
            st.leftIndent = blk.indent_pt
        if blk.space_before_pt:
            st.spaceBefore = blk.space_before_pt
        return st

    story = []
    pending_break = False
    for blk in parse_blocks(doc_html):
        if blk.kind == "pagebreak":
            pending_break = bool(story)
            continue
        if pending_break:
            story.append(PageBreak())
            pending_break = False
        if blk.kind == "table":
            data = []
            for row in blk.rows:
                data.append([Paragraph(_pdf_markup(c.runs) or "&nbsp;", style_for(c)) for c in row])
            if data:
                width = max(len(r) for r in data)
                data = [r + [""] * (width - len(r)) for r in data]
                t = Table(data, hAlign="LEFT")
                t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                story += [t, Spacer(1, 8)]
            continue
        markup = _pdf_markup(blk.runs)
        if blk.list_prefix:
            markup = f"{escape(blk.list_prefix)} {markup}"
        story.append(Paragraph(markup or "&nbsp;", style_for(blk)))

    if not story:
        story.append(Paragraph("&nbsp;", base))
    doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=footer)
    return buf.getvalue()
