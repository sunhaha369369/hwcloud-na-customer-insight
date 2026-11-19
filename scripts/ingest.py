#!/usr/bin/env python
"""Step 1 · 数据接入与标准化。

把 CSV / Excel / SQLite / HTTP API / JSON 等多源数据统一成 7 张标准表，
输出到 <out>/interim/*.csv 并生成 data_quality.json。

用法：
    python scripts/ingest.py --config config.json
    python scripts/ingest.py --config config.json --show-mapping
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import xlsx
from lib.common import (date_norm, ensure_dir, eprint, month_key, month_span, read_csv,
                        read_json, safe_div, to_float, write_csv, write_json)
from lib.schema import ALIAS_INDEX, PRODUCT_LINE_CANON, SCHEMA

TABLE_ORDER = ["accounts", "transactions", "behavior", "feedback", "churn", "competitor", "opportunities"]
FILE_HINTS = {
    "accounts": ["accounts", "account", "客户", "客户主数据"],
    "transactions": ["transactions", "transaction", "billing", "消费", "账单", "交易"],
    "behavior": ["behavior", "behaviour", "usage", "行为", "活跃"],
    "feedback": ["feedback", "ticket", "舆情", "反馈", "工单"],
    "churn": ["churn", "流失"],
    "competitor": ["competitor", "竞品", "竞对"],
    "opportunities": ["opportunities", "opportunity", "商机", "历史机会"],
}


# ------------------------------------------------------------------ 配置

def load_config(path):
    cfg = read_json(path)
    cfg.setdefault("window_months", 12)
    cfg.setdefault("output", {})
    cfg["output"].setdefault("dir", "./insight-output")
    return cfg


# ------------------------------------------------------------------ 各源读取

def _read_source(name, spec):
    """按 type 读取原始记录，返回 list[dict]。"""
    stype = (spec.get("type") or "csv").lower()

    if stype in ("csv", "tsv", "txt"):
        headers, rows = read_csv(spec["path"], delimiter=spec.get("delimiter"))
        return rows

    if stype in ("xlsx", "xls", "excel"):
        sheet = spec.get("sheet")
        return xlsx.read_xlsx(spec["path"], sheet=sheet)

    if stype in ("json", "jsonl"):
        data = read_json(spec["path"])
        if isinstance(data, dict):
            data = _json_path(data, spec.get("records_path", "data"))
        if not isinstance(data, list):
            raise ValueError(f"[{name}] JSON 顶层不是数组，且 records_path 未指向数组")
        return data

    if stype in ("sqlite", "sqlite3", "db"):
        return _read_sqlite(spec)

    if stype in ("api", "http", "rest"):
        return _read_api(name, spec)

    if stype in ("postgres", "postgresql", "mysql"):
        return _read_rdbms(name, spec)

    raise ValueError(f"[{name}] 不支持的数据源类型: {stype}")


def _read_sqlite(spec):
    con = sqlite3.connect(spec["path"])
    con.row_factory = sqlite3.Row
    try:
        sql = spec.get("query") or f'SELECT * FROM "{spec["table"]}"'
        if spec.get("limit"):
            sql = f"SELECT * FROM ({sql}) LIMIT {int(spec['limit'])}"
        cur = con.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def _read_rdbms(name, spec):
    stype = spec.get("type").lower()
    try:
        if stype.startswith("post"):
            import psycopg2  # noqa: F401
            import psycopg2.extras
            conn = psycopg2.connect(spec["dsn"])
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            import pymysql  # noqa: F401
            import pymysql.cursors
            dsn = spec["dsn"]
            conn = pymysql.connect(host=spec.get("host", "localhost"), port=int(spec.get("port", 3306)),
                                   user=spec.get("user"), password=spec.get("password"),
                                   database=spec.get("database"), charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)
        cur = conn.cursor()
        cur.execute(spec["query"])
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except ImportError as exc:
        raise RuntimeError(
            f"[{name}] 缺少驱动（{exc.name}）。两条替代路径：\n"
            f"  1) 装驱动：pip install {'psycopg2-binary' if stype.startswith('post') else 'pymysql'}\n"
            f"  2) 更稳的做法：先用 SQL 导出 CSV，再用 type=csv 接入（推荐，避免生产库凭证进配置文件）"
        ) from exc


def _read_api(name, spec):
    url = spec["url"]
    headers = {"Accept": "application/json", **(spec.get("headers") or {})}
    token = spec.get("token") or os.environ.get(spec.get("token_env", ""), "")
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"

    records, page = [], 1
    max_pages = int(spec.get("max_pages", 1))
    while page <= max_pages:
        params = dict(spec.get("params") or {})
        if spec.get("pagination"):
            p = spec["pagination"]
            params[p.get("param", "page")] = page
            params[p.get("size_param", "pageSize")] = int(p.get("size", 200))
        full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params) if params else url
        req = urllib.request.Request(full, headers=headers,
                                     method=(spec.get("method") or "GET").upper())
        body = spec.get("body")
        if body is not None:
            req.data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=int(spec.get("timeout", 30))) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        chunk = _json_path(payload, spec.get("records_path", "data"))
        if not chunk:
            break
        records.extend(chunk)
        if not spec.get("pagination") or len(chunk) < int(spec["pagination"].get("size", 200)):
            break
        page += 1
    eprint(f"  [api] {name}: 拉取 {len(records)} 条")
    return records


def _json_path(obj, path):
    if not path:
        return obj
    cur = obj
    for seg in str(path).split("."):
        if not seg:
            continue
        if isinstance(cur, list):
            cur = cur[int(seg)] if seg.isdigit() and int(seg) < len(cur) else cur
        elif isinstance(cur, dict):
            if seg in cur:
                cur = cur[seg]
            else:
                return []
        else:
            return []
    return cur


# ------------------------------------------------------------------ 标准化

def _norm_key(s):
    return str(s or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "").replace("（", "(").replace("）", ")")


def map_columns(table, headers, overrides=None):
    """返回 (映射 {原列: 标准字段}, 未识别列清单)。"""
    canon_fields = SCHEMA.get(table, {}).get("fields", {})
    mapping, unmapped = {}, []
    used = set()
    # 第一轮：精确/别名匹配
    for h in headers:
        key = _norm_key(h)
        target = None
        if h in canon_fields:
            target = h
        elif overrides and h in overrides:
            target = overrides[h]
        elif key in ALIAS_INDEX and ALIAS_INDEX[key] in canon_fields:
            target = ALIAS_INDEX[key]
        if target and target not in used:
            mapping[h] = target
            used.add(target)
        else:
            unmapped.append(h)
    # 第二轮：未识别的列，若列名包含已匹配的英文关键字则尝试（如 "amount_cny"）
    still = []
    for h in unmapped:
        key = _norm_key(h)
        hit = None
        for c in canon_fields:
            if c in used:
                continue
            if len(c) >= 4 and c in key:
                hit = c
                break
        if hit:
            mapping[h] = hit
            used.add(hit)
        else:
            still.append(h)
    return mapping, still


def coerce(value, typ):
    if value is None:
        return None
    if typ == "float":
        return to_float(value)
    if typ == "int":
        v = to_float(value)
        return int(v) if v is not None else None
    if typ == "month":
        return month_key(value)
    if typ == "date":
        return date_norm(value)
    if typ == "flag":
        s = str(value).strip().lower()
        if s in ("1", "y", "yes", "true", "是", "na", "t", "✓"):
            return "1"
        if s in ("0", "n", "no", "false", "否", "f"):
            return "0"
        return "1" if s else "0"
    s = str(value).strip()
    return s if s else None


def canon_product_line(v):
    if not v:
        return "其他"
    s = str(v).strip()
    low = s.lower()
    if low in PRODUCT_LINE_CANON:
        return PRODUCT_LINE_CANON[low]
    for k in sorted(PRODUCT_LINE_CANON, key=len, reverse=True):
        if k and k in low:
            return PRODUCT_LINE_CANON[k]
    return s


def normalize_rows(table, rows, overrides=None):
    if not rows:
        return [], {}, []
    headers = []
    for r in rows:
        for k in r:
            if k not in headers:
                headers.append(k)
    mapping, unmapped = map_columns(table, headers, overrides)
    fields = SCHEMA[table]["fields"]
    out = []
    stat = {"date_fail": 0, "month_fail": 0}
    for r in rows:
        rec = {}
        for src, dest in mapping.items():
            val = coerce(r.get(src), fields.get(dest, "str"))
            if fields.get(dest) == "month" and val is None and r.get(src) not in (None, ""):
                stat["month_fail"] += 1
            if dest == "product_line":
                val = canon_product_line(val)
            rec[dest] = val
        # month 兜底：有 date 字段时从日期取月
        if "month" in fields and not rec.get("month"):
            for cand in ("date", "created_date", "churn_date"):
                if rec.get(cand):
                    rec["month"] = month_key(rec[cand])
                    break
        # feedback 无 account_id 时归到"匿名/未关联"
        out.append(rec)
    return out, mapping, unmapped


# ------------------------------------------------------------------ 质量报告

def quality_report(tables, cfg):
    as_of = cfg.get("as_of")
    window = int(cfg.get("window_months", 12))
    months_needed = month_span(as_of, window) if as_of else []
    acc_ids = {r.get("account_id") for r in tables.get("accounts", []) if r.get("account_id")}

    report = {"as_of": as_of, "window_months": window, "tables": {}, "issues": [], "coverage": {}}
    for t in TABLE_ORDER:
        rows = tables.get(t, [])
        fields = SCHEMA[t]["fields"]
        required = SCHEMA[t]["required"]
        info = {"rows": len(rows), "required_missing": [], "empty_fields": []}
        for req in required:
            miss = sum(1 for r in rows if not r.get(req))
            if rows and miss:
                info["required_missing"].append({"field": req, "missing": miss,
                                                 "rate": round(safe_div(miss, len(rows)), 4)})
        for f in fields:
            if not rows:
                break
            empty = sum(1 for r in rows if r.get(f) in (None, ""))
            if empty == len(rows):
                info["empty_fields"].append(f)
        report["tables"][t] = info

    # 时间覆盖
    tx = tables.get("transactions", [])
    tx_months = sorted({r["month"] for r in tx if r.get("month")})
    if tx_months:
        report["coverage"]["transaction_months"] = [tx_months[0], tx_months[-1]]
        report["coverage"]["month_count"] = len(tx_months)
        if months_needed:
            missing = [m for m in months_needed if m not in tx_months]
            report["coverage"]["window_missing_months"] = missing
            if missing:
                report["issues"].append(
                    f"分析窗口内缺 {len(missing)} 个月交易数据（{missing[0]}…{missing[-1]}），"
                    f"同比/环比结论需标注为不完整窗口")
        if len(tx_months) < 6:
            report["issues"].append("交易数据不足 6 个月，趋势与同比结论置信度低，报告须标注")

    # 孤儿交易 / 孤儿反馈
    if acc_ids:
        orphan_tx = {r.get("account_id") for r in tx if r.get("account_id") and r["account_id"] not in acc_ids}
        if orphan_tx:
            report["issues"].append(f"{len(orphan_tx)} 个 account_id 在交易表中存在但客户主数据缺失："
                                    f"{sorted(orphan_tx)[:5]}")
        fb = tables.get("feedback", [])
        unlinked = sum(1 for r in fb if not r.get("account_id") or r["account_id"] not in acc_ids)
        if fb:
            report["coverage"]["feedback_unlinked"] = unlinked
            if unlinked == len(fb):
                report["issues"].append("反馈数据全部无法关联客户，痛点结论只能做全局层面，不可下钻到客户")
            elif unlinked:
                report["issues"].append(f"{unlinked}/{len(fb)} 条反馈未关联客户，已纳入全局痛点分析")

    if not tables.get("transactions"):
        report["issues"].append("缺少交易/消费数据：无法计算分层、机会金额与趋势，"
                               "本报告只能做客户清单级分析，结论不可用于经营决策")
    for t in ("behavior", "feedback", "competitor", "churn"):
        if not tables.get(t):
            report["issues"].append(f"缺少 {t} 数据，相关维度将降级（见报告「数据与口径」章节）")
    return report


# ------------------------------------------------------------------ 主流程

def auto_sources(input_dir):
    """从目录里按文件名猜表，方便"一堆 CSV 直接扔进来"。"""
    found = {}
    if not input_dir or not os.path.isdir(input_dir):
        return found
    files = [f for f in os.listdir(input_dir) if f.lower().endswith((".csv", ".xlsx", ".xls", ".json"))]
    for t, hints in FILE_HINTS.items():
        for f in files:
            low = f.lower()
            if any(h in low for h in hints) and t not in found:
                found[t] = {"type": "xlsx" if low.endswith((".xlsx", ".xls")) else
                            ("json" if low.endswith(".json") else "csv"),
                            "path": os.path.join(input_dir, f)}
    return found


def run(config_path, out_dir=None, show_mapping=False):
    cfg = load_config(config_path)
    out_dir = out_dir or os.path.join(cfg["output"]["dir"], "interim")
    ensure_dir(out_dir)

    sources = {k: v for k, v in (cfg.get("sources") or {}).items() if not k.startswith("_")}
    sources.update({k: v for k, v in auto_sources(cfg.get("input_dir")).items() if k not in sources})
    if not sources:
        raise SystemExit("没有可用的数据源：请在配置里填 sources 或 input_dir")

    eprint(f"→ 接入 {len(sources)} 个数据源")
    tables, mappings = {}, {}
    for t in TABLE_ORDER:
        spec = sources.get(t)
        if not spec:
            tables[t] = []
            continue
        raw = _read_source(t, spec)
        rows, mapping, unmapped = normalize_rows(t, raw, (cfg.get("field_overrides") or {}).get(t))
        tables[t] = rows
        mappings[t] = {"source": spec.get("path") or spec.get("url") or spec.get("table"),
                       "mapped": mapping, "unmapped_columns": unmapped}
        eprint(f"  [{t}] {len(raw)} 行 → {len(rows)} 行，识别 {len(mapping)} 列"
               + (f"，未识别 {len(unmapped)} 列" if unmapped else ""))
        if show_mapping:
            for src, dest in mapping.items():
                eprint(f"       {src} → {dest}")
            if unmapped:
                eprint(f"       未识别: {unmapped}")
        write_csv(os.path.join(out_dir, f"{t}.csv"),
                  list(SCHEMA[t]["fields"].keys()),
                  [{k: r.get(k) for k in SCHEMA[t]["fields"]} for r in rows])

    # as_of 兜底
    if not cfg.get("as_of"):
        tx_months = sorted({r["month"] for r in tables.get("transactions", []) if r.get("month")})
        cfg["as_of"] = tx_months[-1] if tx_months else None
        if not cfg["as_of"]:
            raise SystemExit("无法推断分析基准月（as_of），请在配置里显式指定，如 \"as_of\": \"2026-06\"")

    report = quality_report(tables, cfg)
    report["source_files"] = mappings
    write_json(os.path.join(out_dir, "data_quality.json"), report)
    write_json(os.path.join(out_dir, "runtime_config.json"), cfg)

    for issue in report["issues"]:
        eprint(f"  ⚠ {issue}")
    eprint(f"✓ 标准化完成 → {out_dir}")
    return tables, report, cfg


def main():
    ap = argparse.ArgumentParser(description="Step1 数据接入与标准化")
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--show-mapping", action="store_true")
    a = ap.parse_args()
    run(a.config, a.out, a.show_mapping)


if __name__ == "__main__":
    main()
