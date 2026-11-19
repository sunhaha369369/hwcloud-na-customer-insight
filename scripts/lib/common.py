"""通用 IO / 数值 / 月份工具。纯标准库实现，避免对 pandas 的依赖。"""

import csv
import io
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------- 基础输出

def eprint(*args):
    print(*args, file=sys.stderr)


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------- JSON

def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, obj):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


# ---------------------------------------------------------------- CSV

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp936")


def read_csv(path, delimiter=None):
    """读取 CSV，返回 (headers, rows)。rows 为 list[dict]。自动尝试多种编码。"""
    last_err = None
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                sample = f.read(8192)
                f.seek(0)
                delim = delimiter or _sniff_delimiter(sample)
                reader = csv.DictReader(f, delimiter=delim)
                rows = [{(k or "").strip(): (v if v is not None else "") for k, v in r.items()}
                        for r in reader]
                headers = [h.strip() for h in (reader.fieldnames or [])]
                return headers, rows
        except UnicodeDecodeError as exc:  # 换编码重试
            last_err = exc
            continue
        except csv.Error as exc:
            last_err = exc
            continue
    raise RuntimeError(f"无法解析 CSV {path}: {last_err}")


def _sniff_delimiter(sample):
    head = sample.splitlines()[0] if sample.splitlines() else ""
    for cand in ("\t", ";", "|"):
        if head.count(cand) > head.count(","):
            return cand
    return ","


def write_csv(path, headers, rows, bom=True):
    """写 CSV。默认带 BOM，Excel 直接双击不乱码。rows 支持 list[dict] 或 list[list]。"""
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    enc = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", encoding=enc, newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            if isinstance(r, dict):
                w.writerow([_cell(r.get(h)) for h in headers])
            else:
                w.writerow([_cell(v) for v in r])
    return path


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if abs(v - round(v)) < 1e-9:
            return int(round(v))
        return round(v, 4)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


# ---------------------------------------------------------------- 数值

def to_float(x, default=None):
    if x is None:
        return default
    if isinstance(x, (int, float)):
        if isinstance(x, float) and math.isnan(x):
            return default
        return float(x)
    s = str(x).strip()
    if not s:
        return default
    s = s.replace(",", "").replace("￥", "").replace("¥", "").replace("$", "")
    s = s.replace("元", "").replace("%", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    mult = 1.0
    for suffix, m in (("亿", 1e8), ("万", 1e4), ("k", 1e3), ("K", 1e3), ("M", 1e6)):
        if s.endswith(suffix):
            mult = m
            s = s[: -len(suffix)]
            break
    try:
        v = float(s) * mult
        return -v if neg else v
    except ValueError:
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def safe_div(a, b, default=0.0):
    try:
        if b in (0, None):
            return default
        return a / b
    except (TypeError, ZeroDivisionError):
        return default


def median(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def stdev(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return 0.0
    m = mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def quantile(vals, q):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * clamp(q, 0.0, 1.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def pct_rank(vals, v):
    """v 在 vals 中的分位（0~1），并列取中位名次。"""
    vals = [x for x in vals if x is not None]
    if not vals:
        return 0.5
    below = sum(1 for x in vals if x < v)
    equal = sum(1 for x in vals if x == v)
    return (below + 0.5 * equal) / len(vals)


def zscore(v, baseline):
    baseline = [b for b in baseline if b is not None]
    if len(baseline) < 3:
        return 0.0
    sd = stdev(baseline)
    if sd <= 1e-9:
        return 0.0
    return (v - mean(baseline)) / sd


def pct_change(new, old):
    if old in (None, 0):
        return None
    return (new - old) / abs(old)


# ---------------------------------------------------------------- 月份

_MONTH_PATTERNS = (
    re.compile(r"^(\d{4})\s*[-/.年 ]\s*(\d{1,2})"),
    re.compile(r"^(\d{4})(\d{2})$"),
    re.compile(r"^(\d{4})$"),
)


def month_key(value):
    """把各种日期/月份写法归一化为 'YYYY-MM'。无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m")
    s = str(value).strip()
    if not s:
        return None
    for pat in _MONTH_PATTERNS:
        m = pat.match(s)
        if m:
            y = int(m.group(1))
            if pat.groups == 1:
                return f"{y:04d}-01"
            mo = int(m.group(2))
            if 1 <= mo <= 12 and 1990 <= y <= 2100:
                return f"{y:04d}-{mo:02d}"
    # 2026年6月15日
    m = re.search(r"(\d{4})\D+(\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1990 <= y <= 2100:
            return f"{y:04d}-{mo:02d}"
    return None


def month_to_int(m):
    y, mo = m.split("-")
    return int(y) * 12 + int(mo) - 1


def int_to_month(i):
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def month_add(m, n):
    return int_to_month(month_to_int(m) + n)


def month_diff(a, b):
    """a - b，单位月。"""
    return month_to_int(a) - month_to_int(b)


def month_span(end_month, count):
    """返回以 end_month 结尾、长度为 count 的连续月份列表（升序）。"""
    end = month_to_int(end_month)
    return [int_to_month(i) for i in range(end - count + 1, end + 1)]


def month_label(m):
    return f"{int(m[5:7])}月" if m else ""


def month_short(m):
    """2026-03 -> 26/03"""
    return f"{m[2:4]}/{m[5:7]}" if m else ""


def days_between(d1, d2):
    """d1 - d2，单位天；入参为 YYYY-MM-DD 字符串。"""
    try:
        a = datetime.strptime(str(d1)[:10], "%Y-%m-%d").date()
        b = datetime.strptime(str(d2)[:10], "%Y-%m-%d").date()
        return (a - b).days
    except (ValueError, TypeError):
        return None


def date_norm(value):
    """归一化日期为 YYYY-MM-DD，失败返回原值。"""
    if value in (None, ""):
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[: len(fmt) + 2].strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def fmt_money(v, unit_hint=""):
    """金额人性化：12345678 -> 1234.6万"""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.2f}亿"
    if a >= 1e4:
        return f"{v / 1e4:,.1f}万"
    return f"{v:,.0f}"


def fmt_pct(v, digits=1):
    if v is None:
        return "-"
    return f"{v * 100:.{digits}f}%"
