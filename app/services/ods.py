"""Таблица OpenDocument (.ods) — родной формат LibreOffice Calc; открывается и в Excel.

Без внешних библиотек: .ods — это zip с XML внутри. Поддерживаются текст, числа и дата-время,
жирная строка заголовков, ширина столбцов и закреплённая первая строка.
"""
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from xml.sax.saxutils import escape

MIME = "application/vnd.oasis.opendocument.spreadsheet"

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
    'office:version="1.2"'
)

MANIFEST = f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="{MIME}"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""

STYLES = """<office:automatic-styles>
 <number:date-style style:name="Ndt">
  <number:day number:style="long"/><number:text>.</number:text>
  <number:month number:style="long"/><number:text>.</number:text>
  <number:year number:style="long"/><number:text> </number:text>
  <number:hours number:style="long"/><number:text>:</number:text>
  <number:minutes number:style="long"/>
 </number:date-style>
 <style:style style:name="head" style:family="table-cell">
  <style:table-cell-properties fo:background-color="#e8e3f5"/>
  <style:text-properties fo:font-weight="bold"/>
 </style:style>
 <style:style style:name="dt" style:family="table-cell" style:data-style-name="Ndt"/>
 <style:style style:name="wrap" style:family="table-cell">
  <style:table-cell-properties fo:wrap-option="wrap"/>
 </style:style>
{columns}
</office:automatic-styles>"""


@dataclass
class Sheet:
    name: str
    header: list[str]
    widths: list[float]  # ширина столбцов, см
    rows: list[list] = field(default_factory=list)
    wrap: set[int] = field(default_factory=set)  # столбцы с переносом длинного текста


def _cell(value, wrap: bool = False) -> str:
    if value is None or value == "":
        return "<table:table-cell/>"
    if isinstance(value, bool):
        value = "да" if value else "нет"
    if isinstance(value, (int, float)):
        return f'<table:table-cell office:value-type="float" office:value="{value}"><text:p>{value}</text:p></table:table-cell>'
    if isinstance(value, datetime):
        iso = value.strftime("%Y-%m-%dT%H:%M:%S")
        shown = value.strftime("%d.%m.%Y %H:%M")
        return (
            f'<table:table-cell table:style-name="dt" office:value-type="date" office:date-value="{iso}">'
            f"<text:p>{shown}</text:p></table:table-cell>"
        )
    style = ' table:style-name="wrap"' if wrap else ""
    lines = "".join(f"<text:p>{escape(line)}</text:p>" for line in str(value).split("\n"))
    return f'<table:table-cell{style} office:value-type="string">{lines}</table:table-cell>'


def _content(sheets: list[Sheet]) -> str:
    columns = []
    tables = []
    for n, sheet in enumerate(sheets):
        cols = []
        for i, width in enumerate(sheet.widths):
            name = f"co{n}_{i}"
            columns.append(
                f'<style:style style:name="{name}" style:family="table-column">'
                f'<style:table-column-properties style:column-width="{width}cm"/></style:style>'
            )
            cols.append(f'<table:table-column table:style-name="{name}"/>')
        head = "".join(
            f'<table:table-cell table:style-name="head" office:value-type="string"><text:p>{escape(h)}</text:p></table:table-cell>'
            for h in sheet.header
        )
        body = "".join(
            "<table:table-row>" + "".join(_cell(v, i in sheet.wrap) for i, v in enumerate(row)) + "</table:table-row>"
            for row in sheet.rows
        )
        tables.append(
            f'<table:table table:name="{escape(sheet.name)}">{"".join(cols)}'
            f"<table:table-header-rows><table:table-row>{head}</table:table-row></table:table-header-rows>"
            f"{body}</table:table>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {NS}>'
        + STYLES.format(columns="\n".join(columns))
        + f"<office:body><office:spreadsheet>{''.join(tables)}</office:spreadsheet></office:body>"
        "</office:document-content>"
    )


def _settings(sheets: list[Sheet]) -> str:
    """Закрепить строку заголовков на каждом листе."""
    frozen = "".join(
        f'<config:config-item-map-entry config:name="{escape(s.name)}">'
        '<config:config-item config:name="VerticalSplitMode" config:type="short">2</config:config-item>'
        '<config:config-item config:name="VerticalSplitPosition" config:type="int">1</config:config-item>'
        '<config:config-item config:name="ActiveSplitRange" config:type="short">2</config:config-item>'
        '<config:config-item config:name="PositionBottom" config:type="int">1</config:config-item>'
        "</config:config-item-map-entry>"
        for s in sheets
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-settings {NS}><office:settings>'
        '<config:config-item-set config:name="ooo:view-settings"><config:config-item-map-indexed config:name="Views">'
        '<config:config-item-map-entry><config:config-item-map-named config:name="Tables">'
        f"{frozen}</config:config-item-map-named></config:config-item-map-entry>"
        "</config:config-item-map-indexed></config:config-item-set></office:settings></office:document-settings>"
    )


def build(sheets: list[Sheet]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # mimetype — первым и без сжатия: так LibreOffice узнаёт формат
        z.writestr(zipfile.ZipInfo("mimetype"), MIME, compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", MANIFEST, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("content.xml", _content(sheets), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("settings.xml", _settings(sheets), compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
