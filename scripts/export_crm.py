#!/usr/bin/env python
"""Step 4 · 导出可导入 CRM 的机会清单（CSV / XLSX），并做导入前校验。

    python scripts/export_crm.py --analysis ./insight-output/analysis

产出：
  crm_opportunities.csv     中文表头，供业务复核
  crm_opportunities_en.csv  英文表头，供 Salesforce/HubSpot 等直接映射
  crm_opportunities.xlsx    多 Sheet 交付包（机会清单 / 客户分层 / 痛点摘要 / 字段说明）
  crm_export_report.json    校验结果
"""

import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import xlsx
from lib.common import ensure_dir, fmt_money, read_csv, read_json, write_csv, write_json

# 标准字段：(英文 API 名, 中文表头, 说明)
FIELDS = [
    ("opportunity_name", "机会名称", "CRM 中的机会标题，建议保留客户+类型+品类"),
    ("account_id", "客户编号", "与 CRM 账户主键对齐，导入前务必确认映射关系"),
    ("account_name", "客户名称", "客户全称"),
    ("tier", "客户分层", "S/A/B/C，用于分配销售资源"),
    ("industry", "行业", "用于行业打法分组"),
    ("owner", "负责人", "客户经理，导入后应转为 CRM Owner ID"),
    ("opportunity_type", "机会类型", "RENEW_DEFEND/XSELL/UP_CAPACITY 等"),
    ("type_label", "类型名称", "机会类型的中文标签"),
    ("product_line", "产品线", "主推品类"),
    ("stage", "阶段", "导入初始阶段，默认「需求确认」"),
    ("amount", "预估金额", "元；口径见 amount_type"),
    ("amount_type", "金额口径", "预估年化 / 风险敞口 / 预估首单 等"),
    ("currency", "币种", "默认 CNY"),
    ("probability", "赢单概率", "百分比，由机会分与类型推导，可人工调整"),
    ("close_date", "预计成交日期", "按建议时间窗推算"),
    ("priority", "优先级", "A/B/C"),
    ("score", "机会分", "0~100，排序用"),
    ("next_action", "下一步动作", "写清动作与对象，避免「跟进一下」"),
    ("next_action_date", "下一步日期", "建议 3 个工作日内"),
    ("rationale", "判断依据", "为什么识别出这条机会（数据事实）"),
    ("evidence", "数据证据", "支撑该判断的具体数字"),
    ("evidence_level", "证据等级", "事实 / 推断 / 假设，销售据此决定先验证还是直接跟进"),
    ("confidence", "置信度", "高 / 中 / 低"),
    ("renewal_risk", "续约风险", "0~100，高风险的扩张型机会需先做防御"),
    ("source", "机会来源", "固定为客户洞察自动识别，便于回溯"),
]

STAGE_DEFAULT = "需求确认"
PROB_BASE = {"A": 60, "B": 40, "C": 20}
PROB_BY_TYPE = {"RENEW_DEFEND": 75, "WINBACK": 25, "ACQUIRE": 30}


def load(analysis_dir):
    _, opps = read_csv(os.path.join(analysis_dir, "opportunity_list.csv"))
    prof_path = os.path.join(analysis_dir, "account_profile.csv")
    _, prof = read_csv(prof_path) if os.path.exists(prof_path) else ([], [])
    summary = read_json(os.path.join(analysis_dir, "summary.json"))
    return opps, prof, summary


def build_rows(opps, prof, summary):
    by_acc = {p["account_id"]: p for p in prof}
    as_of = summary["meta"]["as_of"]
    base = date(int(as_of[:4]), int(as_of[5:7]), 1) + timedelta(days=32)
    base = base.replace(day=1)
    rows = []
    for o in opps:
        p = by_acc.get(o["account_id"], {})
        prob = PROB_BY_TYPE.get(o["opp_type"]) or PROB_BASE.get(o["priority"], 20)
        close = base + timedelta(days=int(o["window_days"] or 60))
        nxt = date.today() + timedelta(days=3)
        name = f'{o["account_name"]}｜{o["opp_type_label"]}' + (
            f'｜{o["product_line"]}' if o["product_line"] not in ("全品类", "-") else "")
        rows.append({
            "opportunity_name": name[:120],
            "account_id": o["account_id"],
            "account_name": o["account_name"],
            "tier": o["tier"],
            "industry": o["industry"],
            "owner": o["owner"],
            "opportunity_type": o["opp_type"],
            "type_label": o["opp_type_label"],
            "product_line": o["product_line"],
            "stage": STAGE_DEFAULT,
            "amount": round(float(o["est_amount"]), 2),
            "amount_type": o["amount_type"],
            "currency": "CNY",
            "probability": prob,
            "close_date": close.isoformat(),
            "priority": o["priority"],
            "score": float(o["score"] or 0),
            "next_action": o["next_action"],
            "next_action_date": nxt.isoformat(),
            "rationale": o["driver"],
            "evidence": o["evidence"],
            "evidence_level": o.get("evidence_level", ""),
            "confidence": o.get("confidence", ""),
            "renewal_risk": float(o["renewal_risk"] or 0),
            "source": "客户洞察自动识别",
        })
    return rows


def validate(rows, known_accounts):
    issues, seen = [], {}
    for i, r in enumerate(rows, 1):
        tag = f'{r["account_id"]}/{r["type_label"]}'
        if not r["opportunity_name"]:
            issues.append({"level": "ERROR", "row": i, "msg": "机会名称为空"})
        if r["amount"] <= 0:
            issues.append({"level": "ERROR", "row": i, "msg": f"{tag} 金额必须大于 0（当前 {r['amount']}）"})
        if known_accounts and r["account_id"] not in known_accounts:
            issues.append({"level": "ERROR", "row": i, "msg": f"{tag} 客户编号不存在于客户主数据"})
        if not r["owner"] or r["owner"] == "未指派":
            issues.append({"level": "WARN", "row": i, "msg": f"{tag} 未指派负责人，导入后需补 Owner"})
        if r["renewal_risk"] >= 60 and r["opportunity_type"] not in ("RENEW_DEFEND", "WINBACK"):
            issues.append({"level": "WARN", "row": i,
                           "msg": f"{tag} 续约风险 {r['renewal_risk']} 偏高但仍为扩张型机会，建议先做防御"})
        key = (r["account_id"], r["opportunity_type"], r["product_line"])
        if key in seen:
            issues.append({"level": "ERROR", "row": i, "msg": f"{tag} 与第 {seen[key]} 行重复"})
        else:
            seen[key] = i
    errors = sum(1 for x in issues if x["level"] == "ERROR")
    return {"total": len(rows), "errors": errors,
            "warnings": len(issues) - errors, "issues": issues,
            "importable": errors == 0}


def main():
    ap = argparse.ArgumentParser(description="Step4 导出 CRM 机会清单")
    ap.add_argument("--analysis", default="./insight-output/analysis")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out_dir = a.out or a.analysis
    ensure_dir(out_dir)

    opps, prof, summary = load(a.analysis)
    rows = build_rows(opps, prof, summary)
    known = {p["account_id"] for p in prof}
    report = validate(rows, known)

    # 中文表头 CSV
    write_csv(os.path.join(out_dir, "crm_opportunities.csv"),
              [f[1] for f in FIELDS], [{f[1]: r[f[0]] for f in FIELDS} for r in rows])
    # 英文表头 CSV（供系统映射）
    write_csv(os.path.join(out_dir, "crm_opportunities_en.csv"),
              [f[0] for f in FIELDS], rows)

    # 多 Sheet 交付包
    acc_opp = {}
    for r in rows:
        acc_opp[r["account_id"]] = acc_opp.get(r["account_id"], 0) + r["amount"]

    pain_path = os.path.join(a.analysis, "pain_points.csv")
    _, pains = read_csv(pain_path) if os.path.exists(pain_path) else ([], [])
    alert_path = os.path.join(a.analysis, "alerts.csv")
    _, alerts = read_csv(alert_path) if os.path.exists(alert_path) else ([], [])
    xlsx.write_xlsx(os.path.join(out_dir, "crm_opportunities.xlsx"), {
        "机会清单": {"headers": [f[1] for f in FIELDS],
                 "rows": [[r[f[0]] for f in FIELDS] for r in rows]},
        "客户分层": {"headers": ["客户编号", "客户名称", "分层", "生命周期", "活跃度", "画像", "窗口消费", "增速", "续约风险", "机会金额", "负责人"],
                 "rows": [[p.get("account_id", ""), p.get("account_name", ""), p.get("value_tier", ""),
                           p.get("lifecycle", ""), p.get("activity_level", ""), p.get("persona", ""),
                           p.get("rev_window", ""), p.get("growth", ""), p.get("renewal_risk", ""),
                           round(acc_opp.get(p.get("account_id"), 0), 2), p.get("owner", "")]
                          for p in prof]},
        "痛点摘要": {"headers": ["优先级", "痛点域", "提及客户数", "金额暴露", "评分", "代表原话", "建议动作"],
                 "rows": [[x.get("priority", ""), x.get("category", ""), x.get("account_count", ""),
                           x.get("exposure_amount", ""), x.get("priority_score", ""),
                           x.get("top_quote", ""), x.get("suggested_action", "")] for x in pains]},
        "预警清单": {"headers": ["级别", "类型", "客户编号", "客户名称", "指标", "证据", "建议动作"],
                 "rows": [[x.get("level", ""), x.get("alert_type", ""), x.get("account_id", ""),
                           x.get("account_name", ""), x.get("metric", ""),
                           x.get("evidence", ""), x.get("action", "")] for x in alerts]},
        "字段说明": {"headers": ["字段名", "中文名", "说明"],
                 "rows": [[f[0], f[1], f[2]] for f in FIELDS]},
    })

    write_json(os.path.join(out_dir, "crm_export_report.json"), report)

    print(f"→ 机会 {report['total']} 条，金额合计 {fmt_money(sum(r['amount'] for r in rows))}")
    print(f"  错误 {report['errors']} 条 / 提醒 {report['warnings']} 条"
          f"{'（可导入）' if report['importable'] else '（需先修复错误）'}")
    for x in report["issues"][:8]:
        print(f"   [{x['level']}] {x['msg']}")
    print(f"✓ CRM 清单已导出 → {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()
