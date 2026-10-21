"""HTML helpers for Books Pub documents.

* ``sanitize`` — whitelist sanitiser for whatever the Word-like editor posts back.
* ``split_blocks`` — splits a document into its ``data-block`` sections so
  "Update Devotional" can keep the user's edits while renumbering.
* ``parse_blocks`` — flattens sanitised HTML into simple paragraph/run structures
  that the .docx and .pdf exporters render.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = {"br", "hr"}
ALLOWED_TAGS = {
    "p", "div", "span", "br", "hr", "h1", "h2", "h3", "h4",
    "strong", "b", "em", "i", "u", "s", "strike", "sub", "sup",
    "ul", "ol", "li", "blockquote",
    "table", "thead", "tbody", "tr", "td", "th", "a",
}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template"}
GLOBAL_ATTRS = {"class", "style", "data-block"}
TAG_ATTRS = {"a": {"href"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}

_CLASS_OK = re.compile(r"^(pub-[a-z0-9-]+|pb|swm-[a-z0-9-]+)$")
_BLOCK_OK = re.compile(r"^(cover|(day|chapter|section):\d+)$")
_HREF_OK = re.compile(r"^(https?://|mailto:|#)", re.IGNORECASE)
_UNIT = r"-?\d+(\.\d+)?(pt|px|em|rem|%|cm|mm|in)?"
_STYLE_RULES = {
    "text-align": re.compile(r"^(left|right|center|justify)$"),
    "font-family": re.compile(r"^[\w\s,'\"-]{1,120}$"),
    "font-size": re.compile(rf"^{_UNIT}$"),
    "line-height": re.compile(rf"^{_UNIT}$"),
    "color": re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s,.%]+\)|[a-zA-Z]{3,20})$"),
    "background-color": re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s,.%]+\)|[a-zA-Z]{3,20})$"),
    "margin-left": re.compile(rf"^{_UNIT}$"),
    "margin-top": re.compile(rf"^{_UNIT}$"),
    "margin-bottom": re.compile(rf"^{_UNIT}$"),
    "text-indent": re.compile(rf"^{_UNIT}$"),
    "font-weight": re.compile(r"^(normal|bold|[1-9]00)$"),
    "font-style": re.compile(r"^(normal|italic)$"),
    "text-decoration": re.compile(r"^(underline|line-through|none|underline line-through|line-through underline)$"),
    "text-decoration-line": re.compile(r"^(underline|line-through|none|underline line-through|line-through underline)$"),
    "page-break-after": re.compile(r"^(always|auto)$"),
}


def _clean_style(value: str) -> str:
    kept = []
    for decl in value.split(";"):
        if ":" not in decl:
            continue
        prop, val = (x.strip() for x in decl.split(":", 1))
        prop = prop.lower()
        rule = _STYLE_RULES.get(prop)
        if rule and rule.match(val) and "url(" not in val.lower() and "expression" not in val.lower():
            kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def _clean_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str:
    out = []
    allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
    for name, value in attrs:
        name = name.lower()
        value = value or ""
        if name not in allowed:
            continue
        if name == "class":
            value = " ".join(c for c in value.split() if _CLASS_OK.match(c))
        elif name == "style":
            value = _clean_style(value)
        elif name == "data-block":
            if not _BLOCK_OK.match(value):
                continue
        elif name == "href":
            if not _HREF_OK.match(value.strip()):
                continue
            value = value.strip()
        elif name in ("colspan", "rowspan"):
            if not value.isdigit() or not (1 <= int(value) <= 50):
                continue
        if value == "" and name != "href":
            continue
        out.append(f' {name}="{html.escape(value, quote=True)}"')
    return "".join(out)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{_clean_attrs(tag, attrs)}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID and not self.skip and tag in ALLOWED_TAGS:
            self.out.append(f"<{tag}{_clean_attrs(tag, attrs)}>")
        elif tag not in VOID:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED_TAGS or tag in VOID or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize(raw: str) -> str:
    parser = _Sanitizer()
    parser.feed(raw or "")
    parser.close()
    return parser.result()


# ─── Splitting a document into its data-block sections ───────────────

class _Splitter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.key: str | None = None
        self.buf: list[str] = []
        self.blocks: list[tuple[str | None, str]] = []
        self.loose: list[str] = []

    def _emit(self, text: str) -> None:
        (self.buf if self.key is not None or self.depth else self.loose).append(text)

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text() or f"<{tag}>"
        if self.depth == 0 and tag == "div":
            key = dict(attrs).get("data-block")
            if key:
                self._flush_loose()
                self.key, self.buf = key, []
                self.depth = 1
                return
        if tag in VOID:
            self._emit(raw)
            return
        if self.depth:
            self.depth += 1
            self.buf.append(raw)
        else:
            self.loose.append(raw)

    def handle_startendtag(self, tag, attrs):
        self._emit(self.get_starttag_text() or f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.depth:
            self.depth -= 1
            if self.depth == 0 and self.key is not None:
                self.blocks.append((self.key, "".join(self.buf)))
                self.key, self.buf = None, []
            else:
                self.buf.append(f"</{tag}>")
        else:
            self.loose.append(f"</{tag}>")

    def handle_data(self, data):
        self._emit(html.escape(data, quote=False))

    def _flush_loose(self) -> None:
        text = "".join(self.loose)
        if text.strip():
            self.blocks.append((None, text))
        self.loose = []

    def close(self):  # noqa: D102
        super().close()
        self._flush_loose()


def split_blocks(doc: str) -> list[tuple[str | None, str]]:
    """Return ``[(data-block key or None, inner html)]`` for the document's top level."""
    parser = _Splitter()
    parser.feed(doc or "")
    parser.close()
    return parser.blocks


# ─── Flattening for export ───────────────────────────────────────────

@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    sup: bool = False
    sub: bool = False
    color: str | None = None
    size_pt: float | None = None
    font: str | None = None
    link: str | None = None


@dataclass
class Block:
    kind: str  # p | h1..h4 | quote | pagebreak | table
    runs: list[Run] = field(default_factory=list)
    align: str | None = None
    list_prefix: str | None = None
    level: int = 0
    indent_pt: float = 0
    space_before_pt: float = 0
    rows: list[list["Block"]] = field(default_factory=list)  # table: rows of single-paragraph cells


_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "li", "blockquote"}


def _to_pt(value: str) -> float | None:
    m = re.match(r"^(-?\d+(?:\.\d+)?)(pt|px|em|rem|cm|mm|in|%)?$", value.strip())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2) or "pt"
    return {"pt": n, "px": n * 0.75, "em": n * 12, "rem": n * 12, "cm": n * 28.35, "mm": n * 2.835, "in": n * 72, "%": n * 0.12}[unit]


def _parse_style(value: str) -> dict[str, str]:
    out = {}
    for decl in (value or "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


class _Flattener(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.cur: Block | None = None
        self.fmt: list[dict] = [{}]
        self.lists: list[list] = []  # [kind, counter]
        self.table: list[list[Block]] | None = None
        self.row: list[Block] | None = None
        self.cell: Block | None = None

    # formatting stack
    def _push(self, **kw) -> None:
        self.fmt.append({**self.fmt[-1], **kw})

    def _pop(self) -> None:
        if len(self.fmt) > 1:
            self.fmt.pop()

    def _start_block(self, kind: str, attrs: dict[str, str], prefix: str | None = None, level: int = 0) -> None:
        self._end_block()
        style = _parse_style(attrs.get("style", ""))
        indent = _to_pt(style.get("margin-left", "0")) or 0
        before = _to_pt(style.get("margin-top", "0")) or 0
        self.cur = Block(kind, align=style.get("text-align"), list_prefix=prefix, level=level,
                         indent_pt=indent, space_before_pt=before)

    def _end_block(self) -> None:
        blk, self.cur = self.cur, None
        if blk is None:
            return
        if self.cell is not None:
            if self.cell.runs and blk.runs:
                self.cell.runs.append(Run("\n"))
            self.cell.runs.extend(blk.runs)
            return
        if blk.runs or blk.kind == "pagebreak":
            self.blocks.append(blk)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        style = _parse_style(a.get("style", ""))
        if tag == "hr":
            self._end_block()
            if "pb" in a.get("class", "").split():
                self.blocks.append(Block("pagebreak"))
            return
        if tag == "br":
            if self.cur is None:
                self.cur = Block("p")
            self.cur.runs.append(Run("\n"))
            return
        if tag in ("ul", "ol"):
            self._end_block()
            self.lists.append([tag, 0])
            return
        if tag == "table":
            self._end_block()
            self.table = []
            return
        if tag == "tr" and self.table is not None:
            self.row = []
            return
        if tag in ("td", "th") and self.row is not None:
            self.cell = Block("p", align=style.get("text-align"))
            if tag == "th":
                self._push(bold=True)
            return
        if tag == "li":
            kind = self.lists[-1][0] if self.lists else "ul"
            if self.lists:
                self.lists[-1][1] += 1
            n = self.lists[-1][1] if self.lists else 0
            self._start_block("p", a, prefix=("•" if kind == "ul" else f"{n}."), level=max(len(self.lists) - 1, 0))
            return
        if tag in _BLOCK_TAGS:
            kind = {"blockquote": "quote"}.get(tag, tag if tag.startswith("h") else "p")
            if tag == "div" and self.cell is None:
                self._end_block()
                return
            self._start_block(kind, a)
            if tag.startswith("h"):
                self._push(bold=True)
            return
        # inline formatting
        kw: dict = {}
        if tag in ("strong", "b"):
            kw["bold"] = True
        elif tag in ("em", "i"):
            kw["italic"] = True
        elif tag == "u":
            kw["underline"] = True
        elif tag in ("s", "strike"):
            kw["strike"] = True
        elif tag == "sup":
            kw["sup"] = True
        elif tag == "sub":
            kw["sub"] = True
        elif tag == "a":
            kw["link"] = a.get("href") or None
        if style.get("font-weight") in ("bold", "600", "700", "800", "900"):
            kw["bold"] = True
        if style.get("font-style") == "italic":
            kw["italic"] = True
        deco = style.get("text-decoration", "") + " " + style.get("text-decoration-line", "")
        if "underline" in deco:
            kw["underline"] = True
        if "line-through" in deco:
            kw["strike"] = True
        if style.get("color"):
            kw["color"] = style["color"]
        if style.get("font-size"):
            kw["size_pt"] = _to_pt(style["font-size"])
        if style.get("font-family"):
            kw["font"] = style["font-family"].split(",")[0].strip().strip("'\"")
        self._push(**kw)
        if style.get("text-align") and self.cur is not None and not self.cur.align:
            self.cur.align = style["text-align"]

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self._end_block()
            if self.lists:
                self.lists.pop()
            return
        if tag in ("hr", "br"):
            return
        if tag == "table":
            self._end_block()
            if self.table:
                self.blocks.append(Block("table", rows=self.table))
            self.table = None
            return
        if tag == "tr" and self.table is not None and self.row is not None:
            self._end_block()
            self.table.append(self.row)
            self.row = None
            return
        if tag in ("td", "th") and self.cell is not None:
            self._end_block()
            self.row.append(self.cell)
            self.cell = None
            if tag == "th":
                self._pop()
            return
        if tag in _BLOCK_TAGS:
            self._end_block()
            if tag.startswith("h"):
                self._pop()
            return
        self._pop()

    def handle_data(self, data):
        if not data.strip() and self.cur is None:
            return
        data = re.sub(r"[ \t\r\n]+", " ", data)
        if self.cur is None:
            self.cur = Block("p")
        f = self.fmt[-1]
        self.cur.runs.append(Run(data, **{k: f.get(k) for k in ("bold", "italic", "underline", "strike", "sup", "sub", "color", "size_pt", "font", "link") if f.get(k)}))

    def close(self):  # noqa: D102
        super().close()
        self._end_block()


def parse_blocks(doc: str) -> list[Block]:
    parser = _Flattener()
    parser.feed(doc or "")
    parser.close()
    for blk in parser.blocks:
        if blk.runs:  # trim stray leading/trailing space in each paragraph
            blk.runs[0].text = blk.runs[0].text.lstrip()
            blk.runs[-1].text = blk.runs[-1].text.rstrip()
    return parser.blocks


def plain_text(doc: str) -> str:
    return "\n".join("".join(r.text for r in b.runs) for b in parse_blocks(doc) if b.kind != "pagebreak")


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", text or ""))
