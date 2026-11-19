"""纯标准库的 XLSX 读写。

不依赖 openpyxl / pandas：
- 读：解压 zip，解析 workbook.xml + rels + sharedStrings + styles，识别日期格式。
- 写：内联字符串（inlineStr）方式，生成单/多 sheet 工作簿，带表头冻结与加粗。

够用即可，不追求完整实现 OOXML（不支持公式计算、图表、合并单元格）。
"""

import re
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# 常见日期/时间格式的内置 numFmtId
_DATE_FMT_IDS = set(range(14, 23)) | {27, 28, 29, 30, 31, 34, 35, 36,
                                     45, 46, 47, 50, 51, 52, 53, 54, 55, 56, 57, 58}
_EXCEL_EPOCH = datetime(1899, 12, 30)


def _col_index(ref):
    """'BC12' -> 54（从 0 开始）。"""
    letters = re.match(r"^([A-Z]+)", ref or "")
    if not letters:
        return 0
    idx = 0
    for ch in letters.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _serial_to_str(serial, date1904=False):
    try:
        base = datetime(1904, 1, 1) if date1904 else _EXCEL_EPOCH
        dt = base + timedelta(days=float(serial))
    except (ValueError, TypeError, OverflowError):
        return str(serial)
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _read_shared_strings(zf):
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root.findall(f"{NS_MAIN}si"):
        # 富文本会拆成多个 <r><t>，全部拼接
        parts = [t.text or "" for t in si.iter(f"{NS_MAIN}t")]
        out.append("".join(parts))
    return out


def _read_date_styles(zf):
    """返回 style_index -> 是否日期格式。"""
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return {}
    custom = {}
    for nf in root.iter(f"{NS_MAIN}numFmt"):
        try:
            custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
        except (TypeError, ValueError):
            pass
    result = {}
    cell_xfs = root.find(f"{NS_MAIN}cellXfs")
    if cell_xfs is None:
        return result
    for i, xf in enumerate(cell_xfs.findall(f"{NS_MAIN}xf")):
        try:
            fid = int(xf.get("numFmtId") or 0)
        except ValueError:
            fid = 0
        is_date = fid in _DATE_FMT_IDS
        if not is_date and fid in custom:
            code = custom[fid].lower()
            is_date = bool(re.search(r"(y+|d+|h{1,2}:mm|mm:ss)", code)) and '"' not in code.split(";")[0]
        result[i] = is_date
    return result


def _sheet_targets(zf):
    """返回 [(sheet_name, zip_path)]，按工作簿内顺序。"""
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError:
        return []
    rels = {}
    try:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for r in rel_root:
            rels[r.get("Id")] = r.get("Target")
    except KeyError:
        pass
    targets = []
    for sh in wb.iter(f"{NS_MAIN}sheet"):
        target = rels.get(sh.get(f"{NS_REL}id"), "")
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = "xl/" + target.lstrip("./")
        targets.append((sh.get("name") or "Sheet", path))
    return targets


def read_xlsx(path, sheet=None, max_rows=None):
    """读取 xlsx 为 list[dict]（首行作表头）。sheet 可传名称或序号（从 0 开始）。"""
    with zipfile.ZipFile(path) as zf:
        targets = _sheet_targets(zf)
        if not targets:
            return []
        if sheet is None:
            name, target = targets[0]
        elif isinstance(sheet, int):
            name, target = targets[sheet]
        else:
            hit = [t for t in targets if t[0] == sheet]
            if not hit:
                raise ValueError(f"工作表不存在: {sheet}，可选: {[t[0] for t in targets]}")
            name, target = hit[0]
        try:
            data = zf.read(target)
        except KeyError:
            return []
        shared = _read_shared_strings(zf)
        date_styles = _read_date_styles(zf)
        try:
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            pr = wb.find(f"{NS_MAIN}workbookPr")
            date1904 = (pr is not None and (pr.get("date1904") in ("1", "true")))
        except KeyError:
            date1904 = False

    root = ET.fromstring(data)
    rows = []
    for row in root.iter(f"{NS_MAIN}row"):
        cells = {}
        for c in row.findall(f"{NS_MAIN}c"):
            ref = c.get("r") or ""
            col = _col_index(ref)
            t = c.get("t")
            s = c.get("s")
            is_date = date_styles.get(int(s), False) if s and s.isdigit() else False
            v = c.find(f"{NS_MAIN}v")
            if t == "inlineStr":
                node = c.find(f"{NS_MAIN}is")
                val = "".join(x.text or "" for x in node.iter(f"{NS_MAIN}t")) if node is not None else ""
            elif t == "s":
                try:
                    val = shared[int(v.text)] if v is not None else ""
                except (IndexError, TypeError, ValueError):
                    val = ""
            elif t in ("str", "e"):
                val = v.text if v is not None else ""
            elif t == "b":
                val = "1" if v is not None and v.text == "1" else "0"
            else:
                if v is None or v.text is None:
                    val = ""
                elif is_date:
                    val = _serial_to_str(v.text, date1904)
                else:
                    val = v.text
            cells[col] = val
        if not cells:
            continue
        width = max(cells) + 1
        rows.append([cells.get(i, "") for i in range(width)])
        if max_rows and len(rows) >= max_rows + 1:
            break

    if not rows:
        return []
    headers = [(str(h).strip() if h is not None else f"col{i}") for i, h in enumerate(rows[0])]
    out = []
    for r in rows[1:]:
        r = list(r) + [""] * (len(headers) - len(r))
        rec = {}
        for i, h in enumerate(headers):
            if h == "":
                h = f"col{i}"
            rec[h] = r[i]
        out.append(rec)
    return out


def list_sheets(path):
    with zipfile.ZipFile(path) as zf:
        return [t[0] for t in _sheet_targets(zf)]


# ------------------------------------------------------------------ 写

def _esc(s):
    s = "" if s is None else str(s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;")


def _col_letter(i):
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


_CONTENT_TYPES_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="10"/><name val="微软雅黑"/></font>
<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="微软雅黑"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF2B5B8C"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
</styleSheet>"""


def write_xlsx(path, sheets):
    """写多 sheet xlsx。

    sheets: dict{sheet_name: {"headers": [...], "rows": [[...]]}} 或 list[(name, headers, rows)]
    """
    if isinstance(sheets, dict):
        items = [(k, v.get("headers", []), v.get("rows", [])) for k, v in sheets.items()]
    else:
        items = list(sheets)

    ct_extra = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        for i in range(len(items))
    ) + "</Types>\n"

    wb_sheets = "".join(
        f'<sheet name="{_esc(name[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, (name, _, _) in enumerate(items)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{wb_sheets}</sheets></workbook>"
    )

    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(items))
        )
        + f'<Relationship Id="rId{len(items) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_HEAD + ct_extra)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", _STYLES)
        for i, (name, headers, rows) in enumerate(items):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(headers, rows))
    return path


def _sheet_xml(headers, rows):
    ncols = max([len(headers)] + [len(r) for r in rows]) if (headers or rows) else 0
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{_col_width(i, ncols)}" customWidth="1"/>'
        for i in range(ncols)
    )
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>",
        f'<sheetFormatPr defaultRowHeight="15"/><cols>{cols}</cols><sheetData>',
    ]
    if headers:
        out.append(_row_xml(1, headers, style=1))
    for r_i, row in enumerate(rows, start=2):
        out.append(_row_xml(r_i, row))
    out.append("</sheetData></worksheet>")
    return "".join(out)


def _col_width(i, ncols):
    return 42 if i == 0 else 18


def _row_xml(r_i, values, style=0):
    cells = []
    for c_i, v in enumerate(values):
        ref = f"{_col_letter(c_i)}{r_i}"
        s_attr = f' s="{style}"' if style else ""
        if v is None or v == "":
            if style:
                cells.append(f'<c r="{ref}"{s_attr}/>')
            continue
        if isinstance(v, bool):
            cells.append(f'<c r="{ref}"{s_attr} t="b"><v>{1 if v else 0}</v></c>')
        elif isinstance(v, (int, float)):
            cells.append(f'<c r="{ref}"{s_attr}><v>{v}</v></c>')
        else:
            cells.append(f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{_esc(v)}</t></is></c>')
    return f'<row r="{r_i}">{"".join(cells)}</row>'
