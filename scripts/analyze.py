#!/usr/bin/env python
"""Step 2 · 客户分层画像 / 痛点挖掘 / 机会点识别 / 趋势预警。

输入：<interim>/*.csv（ingest 产出）+ runtime_config.json
输出：<out>/account_profile.csv, pain_points.csv, opportunity_list.csv, alerts.csv, summary.json

所有评分公式与阈值见 references/ 下的对应文档，代码与文档必须保持一致。
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (clamp, date_norm, days_between, ensure_dir, eprint, fmt_money, mean,
                        median, month_add, month_diff, month_span, pct_change, pct_rank,
                        quantile, read_csv, read_json, safe_div, stdev, to_float, write_csv,
                        write_json)
from lib.schema import ACTIVITY_LABELS, LIFECYCLE_ORDER, VALUE_TIER_LABEL, VALUE_TIERS

# ==================================================================== 默认参数

DEFAULTS = {
    "window_months": 12,
    "tier_percentile": {"S": 0.85, "A": 0.65, "B": 0.35},   # 按窗口消费额分位
    "tier_amount": {},                                        # 绝对金额阈值优先于分位，如 {"S": 5e7, "A": 1e7}
    "growth_fast": 0.15,
    "growth_decline": -0.10,
    "activity_cut": {"high": 0.70, "mid": 0.45, "low": 0.20},
    "renewal_urgent_days": 90,
    "renewal_watch_days": 180,
    "pain_weights": {"breadth": 0.30, "intensity": 0.20, "exposure": 0.25,
                     "severity": 0.15, "actionability": 0.10},
    "pain_priority_cut": {"P0": 70, "P1": 50},
    "opp_weights": {"cross_sell": 0.30, "up_sell": 0.30, "white_space": 0.20, "advocacy": 0.20},
    "opp_defend_penalty": 0.40,
    "opp_priority_cut": {"A": 70, "B": 50},
    "win_rate": 0.30,
    "top_opps": 30,
    "alert_z_threshold": 2.0,
}

PRODUCT_UNIVERSE = ["计算", "存储", "网络", "数据库", "容器", "安全", "大数据", "AI", "PaaS", "SaaS", "灾备", "服务"]
SCENARIO_PRODUCTS = ["AI", "大数据", "容器", "安全", "灾备", "PaaS"]

# ---------------------------------------------------------------- 痛点分类法

PAIN_TAXONOMY = [
    ("价格与成本", 0.85, ["价格", "贵", "成本", "折扣", "报价", "账单", "超支", "预算", "性价比", "贵了",
                     "price", "cost", "expensive", "discount", "billing", "降价"]),
    ("性能与稳定性", 0.80, ["性能", "慢", "卡顿", "延迟", "抖动", "丢包", "宕机", "故障", "不稳定", "超时",
                       "限流", "带宽不足", "断连", "performance", "latency", "timeout", "outage", "down"]),
    ("服务响应与支持", 0.90, ["响应慢", "工单", "支持", "客服", "不及时", "没人管", "态度", "售后", "拖延",
                         "sla", "support", "ticket", "处理慢"]),
    ("产品能力缺口", 0.40, ["不支持", "缺少", "没有这个功能", "功能", "能力不足", "无法满足", "不兼容",
                       "缺失", "做不到", "feature", "missing", "not support", "达不到"]),
    ("文档与上手体验", 0.85, ["文档", "教程", "指引", "上手", "学习成本", "示例", "难用", "复杂", "不清晰",
                         "documentation", "guide", "usability", "配置繁琐"]),
    ("生态与集成", 0.55, ["生态", "集成", "对接", "sdk", "中间件", "适配", "兼容性", "api不", "集成难",
                     "integration", "migrate", "ecosystem"]),
    ("合规与安全", 0.70, ["合规", "等保", "安全", "审计", "资质", "数据出境", "密码", "认证", "信创",
                     "compliance", "security", "audit"]),
    ("账号与权限", 0.80, ["权限", "账号", "登录失败", "子账号", "iam", "认证失败", "授权", "配额申请"]),
    ("计费与合同", 0.75, ["合同", "开票", "发票", "结算", "账期", "欠费", "退订", "续签流程",
                     "invoice", "contract", "settlement"]),
    ("迁移与切换成本", 0.50, ["迁移", "切换", "割接", "改造", "迁云", "迁移成本", "migration", "switch"]),
]
PAIN_OTHER = ("其他/未归类", 0.60, [])
_PAIN_ACTION = {
    "价格与成本": "输出 TCO 对比与节省计划/资源包测算，给出阶梯折扣或承诺消费方案，锁定预算窗口",
    "性能与稳定性": "联合 SRE 做架构与容量复盘，出具优化清单与 SLA 保障说明，必要时安排规格升级",
    "服务响应与支持": "升级服务等级（企业级支持/专属 TAM），补齐响应时效承诺与升级通道",
    "产品能力缺口": "产品经理介入做需求评估，给出路线图时间点或等价替代方案，避免空口承诺",
    "文档与上手体验": "提供场景化最佳实践与迁移指南，安排专场培训，沉淀可复用模板",
    "生态与集成": "安排解决方案架构师做集成验证（POC），提供 SDK 与 ISV 伙伴资源",
    "合规与安全": "输出等保/密评/信创合规矩阵与资质清单，推动安全方案立项",
    "账号与权限": "梳理 IAM 权限模型与组织架构映射，提供一次性配置服务",
    "计费与合同": "对齐账单口径与商务条款，处理历史争议，前置续约谈判节奏",
    "迁移与切换成本": "提供迁移工具链与迁移服务报价，用分批迁移降低切换风险",
    "其他/未归类": "补齐反馈分类字段，人工标注后再纳入共性分析",
}

NEG_WORDS = ["慢", "差", "不好", "不行", "不满", "失望", "投诉", "故障", "失败", "贵", "难用", "复杂",
             "卡", "崩", "丢", "降级", "延误", "拖延", "没人", "不稳定", "超支", "不合理", "无法"]
POS_WORDS = ["好", "满意", "快", "稳定", "专业", "及时", "推荐", "优秀", "感谢", "顺畅", "省心", "认可"]


# ==================================================================== 工具

def load_tables(interim_dir):
    tables = {}
    for t in ("accounts", "transactions", "behavior", "feedback", "churn", "competitor", "opportunities"):
        path = os.path.join(interim_dir, f"{t}.csv")
        if os.path.exists(path):
            _, rows = read_csv(path)
            tables[t] = rows
        else:
            tables[t] = []
    return tables


def cfg_get(cfg, key, default=None):
    if key in cfg:
        return cfg[key]
    return DEFAULTS.get(key, default)


# ==================================================================== 指标

def build_metrics(tables, cfg):
    as_of = cfg["as_of"]
    window = int(cfg_get(cfg, "window_months"))
    months = month_span(as_of, window)
    months_prev = month_span(month_add(as_of, -window), window)
    all_months = sorted({r["month"] for r in tables["transactions"] if r.get("month")} |
                        {r["month"] for r in tables["behavior"] if r.get("month")})
    mset = set(months)

    accounts = {}
    for r in tables["accounts"]:
        aid = r.get("account_id")
        if not aid:
            continue
        accounts[aid] = {
            "account_id": aid,
            "account_name": r.get("account_name") or aid,
            "industry": r.get("industry") or "未标注行业",
            "region": r.get("region") or "未标注区域",
            "na_flag": r.get("na_flag") == "1",
            "owner": r.get("owner") or "未指派",
            "sign_date": r.get("sign_date"),
            "contract_end_date": r.get("contract_end_date"),
            "declared_tier": r.get("declared_tier"),
            "employee_count": r.get("employee_count"),
            "revenue_band": r.get("revenue_band"),
        }

    # 交易表出现但主数据缺失的客户，补一条最小档案，避免丢数
    for r in tables["transactions"]:
        aid = r.get("account_id")
        if aid and aid not in accounts:
            accounts[aid] = {"account_id": aid, "account_name": aid, "industry": "未标注行业",
                             "region": "未标注区域", "na_flag": False, "owner": "未指派",
                             "sign_date": None, "contract_end_date": None, "declared_tier": None,
                             "employee_count": None, "revenue_band": None,
                             "_no_master": True}

    for aid, a in accounts.items():
        a["rev_month"] = {}
        a["prod_month"] = {}
        a["prod_rev"] = {}
        a["beh"] = {}
        a["behavior_months"] = 0

    for r in tables["transactions"]:
        aid = r.get("account_id")
        if aid not in accounts:
            continue
        m = r.get("month")
        amt = to_float(r.get("amount"), 0.0) or 0.0
        pl = r.get("product_line") or "其他"
        if m:
            accounts[aid]["rev_month"][m] = accounts[aid]["rev_month"].get(m, 0.0) + amt
            pm = accounts[aid]["prod_month"].setdefault(pl, {})
            pm[m] = pm.get(m, 0.0) + amt
            if m in mset:
                accounts[aid]["prod_rev"][pl] = accounts[aid]["prod_rev"].get(pl, 0.0) + amt

    for r in tables["behavior"]:
        aid = r.get("account_id")
        if aid not in accounts or not r.get("month"):
            continue
        b = accounts[aid]["beh"].setdefault(r["month"], {"mau": 0, "logins": 0, "api": 0.0,
                                                        "res": 0, "new_res": 0, "tickets": 0,
                                                        "usage": [], "trials": 0})
        b["mau"] += to_float(r.get("mau"), 0) or 0
        b["logins"] += to_float(r.get("console_logins"), 0) or 0
        b["api"] += to_float(r.get("api_calls"), 0) or 0
        b["res"] = max(b["res"], to_float(r.get("resource_count"), 0) or 0)
        b["new_res"] += to_float(r.get("new_resource_count"), 0) or 0
        b["tickets"] += to_float(r.get("ticket_count"), 0) or 0
        u = to_float(r.get("usage_ratio"))
        if u is not None:
            b["usage"].append(u if u <= 1 else u / 100.0)
        b["trials"] += to_float(r.get("trial_count"), 0) or 0
    for aid, a in accounts.items():
        a["behavior_months"] = len(a["beh"])

    # 反馈 & 流失原因
    fb_by_acc = {}
    for r in tables["feedback"]:
        rec = dict(r)
        rec["_sent"] = parse_sentiment(r.get("sentiment"), r.get("text"),
                                      r.get("source") or r.get("channel"))
        rec["_cat"] = classify_pain(r.get("category"), r.get("text"))
        rec["_sev"] = parse_severity(r.get("severity"))
        rec["_src"] = r.get("source") or r.get("channel") or "未标注来源"
        aid = r.get("account_id")
        if aid:
            fb_by_acc.setdefault(aid, []).append(rec)
    for r in tables["churn"]:
        aid = r.get("account_id")
        if not aid:
            continue
        rec = {"account_id": aid, "text": r.get("reason_text") or "", "date": r.get("churn_date"),
               "source": "流失原因", "_sent": -0.8, "_sev": 1.0,
               "_cat": classify_pain(r.get("reason_code"), r.get("reason_text")),
               "_lost": to_float(r.get("lost_amount"), 0.0) or 0.0, "escalated": "0"}
        fb_by_acc.setdefault(aid, []).append(rec)

    comp_by_acc = {}
    for r in tables["competitor"]:
        aid = r.get("account_id")
        if aid:
            comp_by_acc.setdefault(aid, []).append(r)

    churn_acc = {}
    for r in tables["churn"]:
        if r.get("account_id"):
            churn_acc.setdefault(r["account_id"], r)

    for aid, a in accounts.items():
        rev_w = sum(v for m, v in a["rev_month"].items() if m in mset)
        rev_prev = sum(v for m, v in a["rev_month"].items() if m in set(months_prev))
        a["rev_window"] = rev_w
        a["rev_prev"] = rev_prev
        a["active_months"] = sum(1 for m in months if a["rev_month"].get(m, 0) > 0)
        last_rev_month = max([m for m, v in a["rev_month"].items() if v > 0], default=None)
        a["last_rev_month"] = last_rev_month
        a["recency_months"] = month_diff(as_of, last_rev_month) if last_rev_month else window * 2

        # 同比 / 趋势
        prev_months_present = [m for m in months_prev if a["rev_month"].get(m, 0) > 0]
        if rev_prev > 0 and len(prev_months_present) >= max(3, window // 2):
            a["growth"] = rev_w / rev_prev - 1
            a["growth_basis"] = "同比"
        else:
            h1 = sum(a["rev_month"].get(m, 0) for m in months[: window // 2])
            h2 = sum(a["rev_month"].get(m, 0) for m in months[window // 2:])
            if h1 > 0:
                ratio = (h2 // max(1, window - window // 2)) / max(1e-9, h1 / max(1, window // 2))
                a["growth"] = ratio - 1
            else:
                a["growth"] = 0.0
            a["growth_basis"] = "窗口内趋势" if h1 > 0 else "数据不足"

        # 行为指标
        beh3 = [a["beh"][m] for m in months[-3:] if m in a["beh"]]
        a["mau_avg_3m"] = mean([b["mau"] for b in beh3])
        a["login_avg_3m"] = mean([b["logins"] for b in beh3])
        a["api_avg_3m"] = mean([b["api"] for b in beh3])
        a["ticket_3m"] = sum(b["tickets"] for b in beh3)
        usage_vals = [u for b in beh3 for u in b["usage"]]
        a["usage_avg_3m"] = mean(usage_vals) if usage_vals else None
        last_beh = a["beh"].get(months[-1]) or (a["beh"][sorted(a["beh"])[-1]] if a["beh"] else None)
        a["resource_count"] = last_beh["res"] if last_beh else 0
        a["new_res_3m"] = sum(b["new_res"] for b in beh3)
        a["trials_3m"] = sum(b["trials"] for b in beh3)
        prev_beh3 = [a["beh"][m] for m in months[-6:-3] if m in a["beh"]]
        a["mau_prev_avg"] = mean([b["mau"] for b in prev_beh3]) if prev_beh3 else None
        a["res_growth"] = (pct_change(a["resource_count"], prev_beh3[-1]["res"])
                           if prev_beh3 and prev_beh3[-1]["res"] else None)

        # 反馈指标
        fbs = fb_by_acc.get(aid, [])
        a["feedback_n"] = len(fbs)
        recent = [f for f in fbs if not f.get("date") or
                  (date_norm(f.get("date")) or "") >= month_add(as_of, -3) + "-01"]
        a["sent_avg"] = mean([f["_sent"] for f in recent]) if recent else None
        neg = [f for f in recent if f["_sent"] is not None and f["_sent"] < -0.15]
        a["recent_n"] = len(recent)
        a["neg_ratio_raw"] = safe_div(len(neg), len(recent)) if recent else None
        # 贝叶斯平滑：样本量小时不要因为 2 条负面就判 100% 负面
        a["neg_ratio"] = round((len(neg) + 0.4) / (len(recent) + 2), 3) if recent else None
        a["esc_ratio"] = safe_div(sum(1 for f in fbs if str(f.get("escalated")) == "1"), len(fbs)) if fbs else None
        nps_vals = [to_float(f.get("nps")) for f in fbs if to_float(f.get("nps")) is not None]
        a["nps"] = mean(nps_vals) if nps_vals else None
        a["top_pain"] = max(fbs, key=lambda f: (f["_sev"], -f["_sent"]))["_cat"] if fbs else None
        a["pain_cats"] = sorted({f["_cat"] for f in fbs})

        # 竞品
        comps = comp_by_acc.get(aid, [])
        a["competitor_n"] = len(comps)
        a["competitor_names"] = sorted({c.get("competitor") for c in comps if c.get("competitor")})
        conf_map = {"高": 0.9, "中": 0.6, "低": 0.3, "high": 0.9, "medium": 0.6, "low": 0.3}
        a["competitor_strength"] = max(
            [conf_map.get(str(c.get("confidence", "")).strip().lower(), 0.5) for c in comps], default=0.0)
        a["competitor_evidence"] = "；".join(
            [str(c.get("evidence") or c.get("signal_type") or "")[:40] for c in comps if c.get("evidence")][:2])

        # 合同 / 续约点
        a["contract_end"] = a["contract_end_date"]
        a["contract_end_estimated"] = False
        a["days_to_renewal"] = (days_between(a["contract_end_date"], as_of + "-01")
                               if a["contract_end_date"] else None)
        if a["days_to_renewal"] is None and a["sign_date"]:
            # 无明确到期日：按签约周年推断下一个续约点，并打上"估算"标记
            anniv = (date_norm(a["sign_date"]) or "")[:7]
            if len(anniv) == 7 and anniv[5:7].isdigit():
                cycles = max(1, month_diff(as_of, anniv) // 12 + 1)
                next_month = month_add(anniv, cycles * 12)
                a["days_to_renewal"] = month_diff(next_month, as_of) * 30
                a["contract_end"] = next_month + "-01"
                a["contract_end_estimated"] = True
        a["is_churned"] = aid in churn_acc
        a["churn_reason"] = (churn_acc.get(aid) or {}).get("reason_text")
        a["lost_amount"] = to_float((churn_acc.get(aid) or {}).get("lost_amount"), 0.0) or 0.0
        first_rev = min([m for m, v in a["rev_month"].items() if v > 0], default=None)
        base_start = date_norm(a["sign_date"])[:7] if a["sign_date"] else (first_rev or as_of)
        a["tenure_months"] = max(0, month_diff(as_of, base_start)) if base_start else 0
        a["coverage"] = sorted(a["prod_rev"].keys())
    return accounts, {"months": months, "months_prev": months_prev, "all_months": all_months,
                      "feedback_by_acc": fb_by_acc, "competitor_by_acc": comp_by_acc}


# ---------------------------------------------------------------- 解析器

# 渠道先验：不同触点天然带情绪倾向，缺少情感字段时用于校正文本词典结果
SOURCE_PRIOR = [
    ("流失", -0.85), ("投诉", -0.60), ("工单", -0.35), ("舆情", -0.35), ("社区", -0.30),
    ("拜访", -0.20), ("nps", -0.15), ("调研", -0.15), ("问卷", -0.15), ("回访", -0.15),
]


def _source_prior(source):
    if not source:
        return 0.0
    s = str(source).lower()
    for key, val in SOURCE_PRIOR:
        if key in s:
            return val
    return 0.0


def parse_sentiment(value, text=None, source=None):
    """归一化情感到 [-1, 1]。

    1) 有情感字段：按其刻度换算（-1~1 / 1~5 / 0~10 / 正负中）；
    2) 无情感字段：文本情感词典 + 渠道先验加权（工单/投诉/流失原因天然偏负）。
    """
    if value not in (None, ""):
        s = str(value).strip().lower()
        mapping = {"正": 0.6, "正面": 0.6, "positive": 0.6, "pos": 0.6, "好": 0.6, "满意": 0.7,
                   "中": 0.0, "中性": 0.0, "neutral": 0.0, "一般": 0.0,
                   "负": -0.6, "负面": -0.6, "negative": -0.6, "neg": -0.6, "差": -0.7, "不满": -0.8}
        if s in mapping:
            return mapping[s]
        num = to_float(value)
        if num is not None:
            if -1.0 <= num <= 1.0:
                return clamp(num, -1.0, 1.0)
            if 1.0 < num <= 5.0:          # 1~5 分制（3 分为中性）
                return clamp((num - 3) / 2.0, -1.0, 1.0)
            if 5.0 < num <= 10.0:         # 0~10 分制
                return clamp((num - 5) / 5.0, -1.0, 1.0)
    t = str(text or "")
    prior = _source_prior(source)
    neg = sum(1 for w in NEG_WORDS if w in t)
    pos = sum(1 for w in POS_WORDS if w in t)
    if neg == 0 and pos == 0:
        return prior
    lex = clamp((pos - neg) / max(1.0, (pos + neg)) * 0.8, -1.0, 1.0)
    return clamp(0.6 * lex + 0.4 * prior, -1.0, 1.0)

def parse_severity(value):
    if value in (None, ""):
        return 0.5
    s = str(value).strip().lower()
    table = {"p0": 1.0, "p1": 0.75, "p2": 0.45, "p3": 0.25, "致命": 1.0, "严重": 0.9, "高": 0.9,
             "中": 0.5, "一般": 0.5, "低": 0.2, "critical": 1.0, "high": 0.9, "medium": 0.5, "low": 0.2}
    if s in table:
        return table[s]
    num = to_float(value)
    if num is not None:
        if 1.0 <= num <= 5.0:
            return clamp((num - 1) / 4.0, 0.0, 1.0)
        if 0.0 <= num <= 1.0:
            return num
    return 0.5


def classify_pain(category, text):
    """优先信任已有分类字段；否则用关键词词典匹配。"""
    if category:
        c = str(category).strip()
        for canon, _, _ in PAIN_TAXONOMY:
            if c == canon or canon in c or c in canon:
                return canon
    t = str(text or "")
    tl = t.lower()
    best, best_hits = None, 0
    for canon, _, kws in PAIN_TAXONOMY:
        hits = sum(1 for k in kws if k.lower() in tl)
        if hits > best_hits:
            best, best_hits = canon, hits
    if best:
        return best
    return PAIN_OTHER[0]


def actionability(cat, text):
    base = next((a for c, a, _ in PAIN_TAXONOMY if c == cat), 0.5)
    t = str(text or "")
    if any(w in t for w in ("希望", "建议", "要求", "能否", "需要", "计划")):
        base = min(1.0, base + 0.15)
    return base


# ==================================================================== 分层与画像

def assign_tiers(accounts, cfg):
    ids = [a["account_id"] for a in accounts.values() if not a["is_churned"] or a["rev_window"] > 0]
    revs = [accounts[i]["rev_window"] for i in ids]
    pct_cut = cfg_get(cfg, "tier_percentile")
    amt_cut = cfg_get(cfg, "tier_amount") or {}

    for a in accounts.values():
        rev = a["rev_window"]
        tier = "C"
        if amt_cut:
            for t in VALUE_TIERS:                       # 绝对阈值优先
                thr = to_float(amt_cut.get(t))
                if thr is not None and rev >= thr:
                    tier = t
                    break
        else:
            p = pct_rank(revs, rev)
            if p >= pct_cut.get("S", 0.85):
                tier = "S"
            elif p >= pct_cut.get("A", 0.65):
                tier = "A"
            elif p >= pct_cut.get("B", 0.35):
                tier = "B"
        a["value_pct"] = round(pct_rank(revs, rev), 3)
        a["value_tier"] = tier
        a["value_tier_label"] = VALUE_TIER_LABEL[tier]
        a["na_gap"] = bool(a["na_flag"] and tier in ("B", "C"))
        r_p = 1 - clamp(safe_div(a["recency_months"], 12.0), 0, 1)
        f_p = clamp(safe_div(a["active_months"], max(1, len(month_span(cfg["as_of"], int(cfg_get(cfg, "window_months")))))), 0, 1)
        a["rfm"] = {"R": round(r_p * 5, 2), "F": round(f_p * 5, 2),
                    "M": round(a["value_pct"] * 5, 2)}


def assign_activity(accounts, cfg):
    cut = cfg_get(cfg, "activity_cut")
    vals = {}
    for aid, a in accounts.items():
        if a["behavior_months"] == 0:
            vals[aid] = None
            continue
        vals[aid] = {"mau": a["mau_avg_3m"], "login": a["login_avg_3m"], "api": a["api_avg_3m"],
                     "res": a["resource_count"], "usage": a["usage_avg_3m"] or 0.0}
    present = {k: v for k, v in vals.items() if v}
    for aid, a in accounts.items():
        v = vals[aid]
        if not v:
            a["activity_score"] = None
            a["activity_level"] = "数据缺失"
            continue
        score = (0.35 * pct_rank([x["mau"] for x in present.values()], v["mau"])
                 + 0.20 * pct_rank([x["login"] for x in present.values()], v["login"])
                 + 0.20 * pct_rank([x["api"] for x in present.values()], v["api"])
                 + 0.15 * pct_rank([x["res"] for x in present.values()], v["res"])
                 + 0.10 * clamp(v["usage"], 0, 1))
        a["activity_score"] = round(score, 3)
        if score >= cut["high"]:
            a["activity_level"] = ACTIVITY_LABELS[0]
        elif score >= cut["mid"]:
            a["activity_level"] = ACTIVITY_LABELS[1]
        elif score >= cut["low"]:
            a["activity_level"] = ACTIVITY_LABELS[2]
        else:
            a["activity_level"] = ACTIVITY_LABELS[3]


def assign_lifecycle(accounts, cfg, ctx):
    months = ctx["months"]
    fast = cfg_get(cfg, "growth_fast")
    decline = cfg_get(cfg, "growth_decline")
    for a in accounts.values():
        if a["is_churned"]:
            a["lifecycle"] = "已流失"
        elif a["rev_window"] <= 0 and a["rev_prev"] > 0:
            a["lifecycle"] = "已流失"
        elif a["rev_window"] <= 0:
            a["lifecycle"] = "潜在客户"
        elif a["tenure_months"] <= 6:
            a["lifecycle"] = "新签爬坡期"
        else:
            seq = [a["rev_month"].get(m, 0) for m in months]
            drops = 0
            for i in range(len(seq) - 1, 0, -1):
                if seq[i] < seq[i - 1]:
                    drops += 1
                else:
                    break
            cum = safe_div(seq[-1] - seq[max(0, len(seq) - 1 - drops)], abs(seq[max(0, len(seq) - 1 - drops)]) or 1)
            mau_drop = (a["mau_prev_avg"] and a["mau_avg_3m"] < a["mau_prev_avg"] * 0.6)
            risky = ((drops >= 3 and cum <= -0.15) or mau_drop
                     or (a["competitor_strength"] >= 0.8 and (a["growth"] or 0) < 0)
                     or ((a["neg_ratio"] or 0) >= 0.6 and (a["days_to_renewal"] is not None
                                                          and a["days_to_renewal"] <= 90)))
            a["slide_months"] = drops
            a["slide_cum"] = round(cum, 3)
            if risky:
                a["lifecycle"] = "流失预警"
            elif (a["growth"] or 0) <= decline:
                a["lifecycle"] = "停滞期"
            elif (a["growth"] or 0) >= fast:
                a["lifecycle"] = "成长期"
            else:
                a["lifecycle"] = "成熟期"
        if a["lifecycle"] in ("新签爬坡期", "成长期") and a["growth"] is None:
            a["growth"] = 0.0


PLAYBOOK = {
    ("S", "high"): ("压舱石·深度经营型", "以钱包份额和联合创新为主线，防止被分割采购",
                    ["高层季度互访 + 年度战略对齐会", "联合方案/联创实验室立项", "预留实例 + 节省计划锁定 3 年用量",
                     "跨 BG 场景复制（同集团其他业务线上云）"], "钱包份额、NPS、续约率", "月度健康度 + 季度高层"),
    ("S", "mid"): ("战略客户·场景渗透型", "用 2~3 个高价值场景打进核心业务，先做深再做广",
                   ["选 1 个业务域做架构评审与迁移规划", "以 POC 换取生产环境落地", "对齐年度预算与采购流程关键人"],
                   "场景渗透数、生产负载占比", "双周"),
    ("S", "low"): ("战略客户·健康度急救型", "先止血：查清活跃下滑与负面根因，再谈扩张",
                   ["48 小时内 TAM 上门做健康度诊断", "输出问题清单与整改时间表", "高管层对齐服务等级与 SLA"],
                   "活跃回升、重大工单归零、续约保底", "周度"),
    ("A", "high"): ("核心客户·扩张型", "品类扩张 + 容量承接，把单点信任变成多点依赖",
                    ["补齐 2 个高渗透品类（详见机会清单）", "容量预警产品提前扩容报价", "行业标杆案例共建"],
                    "品类数、ARPU、净增长额", "月度"),
    ("A", "mid"): ("核心客户·深耕型", "围绕已有场景做深度与稳定性，提升不可替代性",
                   ["场景化最佳实践输出 + 专项培训", "成本优化方案（TCO 对比）换取预算", "推动第二个业务部门立项"],
                   "ARPU、活跃度、满意度", "月度"),
    ("A", "low"): ("核心客户·唤醒型", "活跃与用量双降，先用轻量方案重建使用习惯",
                   ["梳理在用资源与闲置资源", "发放用量激励/试用新品类", "客户成功经理定期回访"],
                   "月活回升、资源数增长", "双周"),
    ("B", "high"): ("成长客户·加速型", "增长势头明确，用打包方案和商务杠杆放大",
                    ["打包方案 + 阶梯返点", "上量承诺换价格", "引导从按需切包年，锁定现金流"],
                    "消费增速、包年占比", "月度"),
    ("B", "mid"): ("成长客户·深耕型", "用量稳定但增速平缓，做深现有场景并培育第二增长点",
                   ["锁定主力业务场景做深度优化", "推动第二个部门/子公司上线", "用年度框架锁定价格与账期"],
                   "ARPU、品类数、续约率", "月度"),
    ("C", "mid"): ("长尾客户·维持型", "低人力投入维持现有关系，靠自助与标准化服务",
                   ["线上自助 + 季度一次主动关怀", "用行业模板降低采纳门槛", "监控用量突变作为升级信号"],
                   "留存率、激活率", "季度"),
    ("B", "low"): ("成长客户·培育型", "低触达高潜力，用标准化方案降低决策成本",
                   ["目录化轻量方案 + 自助开通", "线上训练营/文档自助", "季度一次主动关怀"],
                   "激活率、首单转化", "季度"),
    ("C", "high"): ("长尾客户·自助增长型", "低人力投入，靠产品体验和渠道覆盖",
                    ["全自助开通与计费", "渠道/伙伴承接服务", "自动化用量提醒"],
                    "自助转化率、留存", "季度（自动化为主）"),
    ("C", "low"): ("长尾客户·观察型", "暂无经营价值，保持低成本触达，等待信号触发",
                   ["纳入自动化培育池", "监控用量突变作为升级信号", "不投入专属人力"],
                   "升级转化数", "半年度"),
}
_PLAYBOOK_ACT_MAP = {"高活跃": "high", "中活跃": "mid", "低活跃": "low", "沉睡": "low", "数据缺失": "mid"}


def diagnose(a):
    """一句话根因：说明这个客户为什么是当前状态，客户经理可据此复核。"""
    if a["lifecycle"] == "已流失":
        return "已流失：" + (a.get("churn_reason") or "原因未记录")
    if a["lifecycle"] == "潜在客户":
        if a.get("trials_3m"):
            return f"有试用（近 3 月 {int(a['trials_3m'])} 次）但窗口内零付费"
        return "有账号无付费，尚未形成首个付费场景"

    bits = []
    growth = a["growth"] or 0
    if a.get("slide_months", 0) >= 3:
        bits.append(f"消费连续 {int(a['slide_months'])} 个月下滑（累计 {a.get('slide_cum', 0):+.0%}）")
    elif growth <= -0.10:
        bits.append(f"消费同比 {growth:+.0%}，已进入下降区间")
    elif growth >= 0.20:
        bits.append(f"消费同比 {growth:+.0%}，处于扩张")
    if a["mau_prev_avg"] and a["mau_avg_3m"] < a["mau_prev_avg"] * 0.75:
        bits.append(f"月活较前 3 月降 {1 - a['mau_avg_3m'] / a['mau_prev_avg']:.0%}")
    if (a["neg_ratio"] or 0) >= 0.5 and a.get("recent_n", 0) >= 3:
        bits.append(f"近 3 月负面反馈占 {a['neg_ratio']:.0%}")
    if (a["competitor_strength"] or 0) >= 0.6:
        bits.append(f"竞品动作明确（强度 {a['competitor_strength']:.0%}）")
    if a["days_to_renewal"] is not None and a["days_to_renewal"] <= 90:
        bits.append(f"合同 {int(a['days_to_renewal'])} 天后到期")
    if (a["usage_avg_3m"] or 0) >= 0.85:
        bits.append(f"配额使用率 {a['usage_avg_3m']:.0%} 接近上限")
    if not bits:
        bits.append(f"{a['activity_level']}、消费同比 {growth:+.0%}，未见异常信号")
    return "；".join(bits[:3])


def assign_playbook(accounts):
    for a in accounts.values():
        a["root_cause"] = diagnose(a)
        lc = a["lifecycle"]
        if lc == "已流失":
            a["persona"] = "已流失·赢回候选"
            a["strategy"] = "诊断流失根因，用差异化方案与迁移服务争取回归"
            a["actions"] = ["调取流失原因与竞品报价做对比", "提供迁移补贴/回归优惠", "指定专人做赢回跟进"]
            a["kpi"] = "赢回金额、赢回客户数"
            a["cadence"] = "一次性专项 + 季度回访"
            continue
        if lc == "潜在客户":
            a["persona"] = "潜在客户·转化型"
            a["strategy"] = "有使用无付费或纯试用，用最小闭环促成首单"
            a["actions"] = ["识别试用资源与真实场景", "给出首单优惠与迁移支持", "7 天内跟进试用反馈"]
            a["kpi"] = "首单转化率、试用转正周期"
            a["cadence"] = "双周"
            continue
        act_key = _PLAYBOOK_ACT_MAP.get(a["activity_level"], "mid")
        if lc == "成长期" and act_key == "low":
            act_key = "mid"          # 成长期是前瞻信号，不应因当前活跃度低被归入"观察型"
        key = (a["value_tier"], act_key)
        tag, strategy, actions, kpi, cadence = PLAYBOOK.get(key, PLAYBOOK[("C", "low")])
        if lc in ("停滞期", "流失预警"):
            tag = "防御优先·" + tag
            actions = (["48 小时内完成用量与活跃诊断，锁定流失根因",
                        "准备竞品对比与续约商务方案，一把手层面对话"] + actions[:2])
        if lc == "新签爬坡期":
            tag = tag.replace("型", "·爬坡型")
            actions = ["30 天内完成上线验收与首个场景交付"] + actions
        a["persona"] = tag
        a["strategy"] = strategy
        a["actions"] = actions
        a["kpi"] = kpi
        a["cadence"] = cadence


def renew_risk(a, cfg):
    """续约/流失风险 0~100。权重：合同临近 40% / 活跃趋势 25% / 负面舆情 15% / 竞品 20%。"""
    d = a["days_to_renewal"]
    if d is None:
        prox = 0.30
    elif d <= 60:
        prox = 1.00
    elif d <= 90:
        prox = 0.85
    elif d <= 180:
        prox = 0.55
    elif d <= 365:
        prox = 0.25
    else:
        prox = 0.10
    g = a["growth"] or 0
    act = 0.80 if g <= -0.15 else (0.55 if g < 0 else (0.30 if g < 0.15 else 0.10))
    if a["activity_level"] == "沉睡":
        act = max(act, 0.85)
    neg = clamp(a["neg_ratio"] if a["neg_ratio"] is not None else 0.3, 0, 1)
    comp = a["competitor_strength"] or 0.10
    if a["is_churned"]:
        return 100.0
    risk = 100 * (0.40 * prox + 0.25 * act + 0.15 * neg + 0.20 * comp)
    return round(clamp(risk, 0, 100), 1)


# ==================================================================== 痛点

def analyze_pains(tables, accounts, ctx, cfg):
    months = ctx["months"]
    window_start = months[0] + "-01"
    all_fb = []
    for r in tables["feedback"]:
        all_fb.append({"account_id": r.get("account_id"), "date": date_norm(r.get("date")),
                       "source": r.get("source") or r.get("channel") or "未标注",
                       "text": r.get("text") or "",
                       "sent": parse_sentiment(r.get("sentiment"), r.get("text"),
                                               r.get("source") or r.get("channel")),
                       "cat": classify_pain(r.get("category"), r.get("text")),
                       "sev": parse_severity(r.get("severity")),
                       "escalated": str(r.get("escalated")) == "1",
                       "resolved": str(r.get("resolved")) == "1",
                       "feedback_id": r.get("feedback_id")})
    for r in tables["churn"]:
        if r.get("account_id"):
            all_fb.append({"account_id": r["account_id"], "date": date_norm(r.get("churn_date")),
                           "source": "流失原因", "text": r.get("reason_text") or "",
                           "sent": -0.8, "cat": classify_pain(r.get("reason_code"), r.get("reason_text")),
                           "sev": 1.0, "escalated": True, "resolved": False,
                           "feedback_id": None})

    total_accounts = max(1, len([a for a in accounts.values() if not a.get("_no_master")]))
    groups = {}
    for f in all_fb:
        g = groups.setdefault(f["cat"], {"rows": [], "accounts": set(), "texts": []})
        g["rows"].append(f)
        if f["account_id"] and f["account_id"] in accounts:
            g["accounts"].add(f["account_id"])
        if f["text"]:
            g["texts"].append(f["text"])

    rows = []
    for cat, g in groups.items():
        accs = g["accounts"]
        n = len(g["rows"])
        breadth = safe_div(len(accs), total_accounts)
        intensity = clamp(mean([-f["sent"] for f in g["rows"] if f["sent"] is not None] or [0]), 0, 1)
        exposure = sum(accounts[a]["rev_window"] for a in accs if a in accounts) or \
            sum(accounts[a]["lost_amount"] for a in accs if a in accounts)
        sev = mean([f["sev"] for f in g["rows"]])
        esc = safe_div(sum(1 for f in g["rows"] if f["escalated"]), n)
        sev = clamp(0.75 * sev + 0.25 * esc, 0, 1)
        actb = mean([actionability(cat, f["text"]) for f in g["rows"]])
        tier_counts = {}
        for aid in accs:
            t = accounts[aid]["value_tier"]
            tier_counts[t] = tier_counts.get(t, 0) + 1
        dominant_tier = max(tier_counts, key=tier_counts.get) if tier_counts else "-"
        sources = {}
        for f in g["rows"]:
            sources[f["source"]] = sources.get(f["source"], 0) + 1
        quote = _pick_quote(g["rows"])
        rows.append({
            "category": cat, "mentions": n, "account_count": len(accs),
            "breadth": round(breadth, 3), "intensity": round(intensity, 3),
            "severity": round(sev, 3), "actionability": round(actb, 3),
            "exposure_amount": round(exposure, 2), "dominant_tier": dominant_tier,
            "escalated_ratio": round(esc, 3),
            "sources": "、".join(f"{k}×{v}" for k, v in sorted(sources.items(), key=lambda x: -x[1])[:3]),
            "top_quote": quote, "examples": [f["text"][:60] for f in g["rows"] if f["text"]][:3],
            "suggested_action": _PAIN_ACTION.get(cat, _PAIN_ACTION["其他/未归类"]),
            "sample_accounts": sorted(accs)[:5],
        })

    w = cfg_get(cfg, "pain_weights")
    exposures = [r["exposure_amount"] for r in rows] or [1]
    for r in rows:
        r["exposure_score"] = round(pct_rank(exposures, r["exposure_amount"]), 3)
        score = 100 * (w["breadth"] * r["breadth"] + w["intensity"] * r["intensity"]
                       + w["exposure"] * r["exposure_score"] + w["severity"] * r["severity"]
                       + w["actionability"] * r["actionability"])
        r["priority_score"] = round(score, 1)
        cut = cfg_get(cfg, "pain_priority_cut")
        r["priority"] = "P0" if score >= cut["P0"] else ("P1" if score >= cut["P1"] else "P2")
        r["is_common"] = r["account_count"] >= 2 or r["breadth"] >= 0.2
    rows.sort(key=lambda r: -r["priority_score"])
    for i, r in enumerate(rows, 1):
        r["impact_rank"] = i
    by_volume = sorted(rows, key=lambda r: (-r["mentions"], -r["account_count"]))
    for i, r in enumerate(by_volume, 1):
        r["volume_rank"] = i
    for r in rows:
        # 正数＝「叫得响但不一定最贵」；负数＝「闷声花钱」——后者常被忽略
        r["rank_gap"] = r["volume_rank"] - r["impact_rank"]
        r["evidence_level"] = "事实" if r["account_count"] >= 2 else "个案"
    return rows


def _pick_quote(rows):
    """挑一条最有代表性的原文：严重度高、情感负、信息量足。"""
    cands = [f for f in rows if f.get("text")]
    if not cands:
        return ""
    best = max(cands, key=lambda f: (f["sev"] * 2 + (1 - (f["sent"] if f["sent"] is not None else 0.0))
                                     + min(len(f["text"]), 120) / 240.0))
    return best["text"][:110]


# ==================================================================== 机会点

def peer_stats(accounts, ctx):
    window = len(ctx["months"])
    peers = {}
    for a in accounts.values():
        if a["rev_window"] <= 0:
            continue
        peers.setdefault(a["industry"], []).append(a)
    prod_spend, prod_pen, prod_median, prod_p75 = {}, {}, {}, {}
    for ind, members in peers.items():
        for p in PRODUCT_UNIVERSE:
            vals = [m["prod_rev"].get(p, 0) for m in members]
            spenders = [v for v in vals if v > 0]
            prod_pen[(ind, p)] = safe_div(len(spenders), len(members))
            prod_median[(ind, p)] = median(spenders) if spenders else 0.0
            prod_p75[(ind, p)] = quantile(spenders, 0.75) if spenders else 0.0
        prod_spend[ind] = [m["rev_window"] for m in members]
    # 全国口径（行业内客户不足时兜底）
    for p in PRODUCT_UNIVERSE:
        vals = [a["prod_rev"].get(p, 0) for a in accounts.values() if a["rev_window"] > 0]
        spenders = [v for v in vals if v > 0]
        prod_pen[("__all__", p)] = safe_div(len(spenders), max(1, len([a for a in accounts.values() if a["rev_window"] > 0])))
        prod_median[("__all__", p)] = median(spenders) if spenders else 0.0
        prod_p75[("__all__", p)] = quantile(spenders, 0.75) if spenders else 0.0
    # 同层（价值分层）基准：避免用全盘 P75 去要求一个小客户，导致机会金额虚高
    tier_median, tier_p75 = {}, {}
    for t in VALUE_TIERS:
        revs = [a["rev_window"] for a in accounts.values() if a["value_tier"] == t and a["rev_window"] > 0]
        if revs:
            tier_median[t] = median(revs)
            tier_p75[t] = quantile(revs, 0.75)
    return {"pen": prod_pen, "median": prod_median, "p75": prod_p75, "window": window,
            "tier_median": tier_median, "tier_p75": tier_p75}


def compute_dimensions(accounts, peers, cfg):
    """给每个客户算 4 个机会维度分（0~100）+ 续约风险。"""
    win = max(1, peers["window"])
    for a in accounts.values():
        ind = a["industry"]
        all_revs = [x["rev_window"] for x in accounts.values() if x["rev_window"] > 0] or [1]

        def pen(p):
            v = peers["pen"].get((ind, p))
            if v is None or (v == 0 and peers["pen"].get(("__all__", p), 0) > 0):
                return peers["pen"].get(("__all__", p), 0.0)
            return v

        def pmed(p):
            v = peers["median"].get((ind, p)) or 0
            return v if v > 0 else peers["median"].get(("__all__", p), 0.0)

        covered = set(a["coverage"])
        missing = [p for p in PRODUCT_UNIVERSE if p not in covered]
        gap_pens = [pen(p) for p in missing if pen(p) >= 0.30]
        a["xs_gap_products"] = sorted([p for p in missing if pen(p) >= 0.30], key=lambda p: -pen(p))
        a["cross_sell"] = round(100 * (mean(gap_pens) if gap_pens else 0.0), 1)

        p75 = quantile(all_revs, 0.75)
        gap = clamp(1 - safe_div(a["rev_window"], p75), 0, 1) if a["rev_window"] > 0 else 0.0
        a["up_sell"] = round(100 * gap, 1)
        if (a["usage_avg_3m"] or 0) >= 0.80:
            a["up_sell"] = round(min(100.0, a["up_sell"] + 15), 1)
        if (a["res_growth"] or 0) > 0.20:
            a["up_sell"] = round(min(100.0, a["up_sell"] + 8), 1)

        scen_gaps = [p for p in SCENARIO_PRODUCTS if p not in covered and 0.20 <= pen(p) <= 0.65]
        a["white_space"] = round(100 * (mean([pen(p) for p in scen_gaps]) if scen_gaps else 0.0), 1)
        a["ws_products"] = scen_gaps

        if a["nps"] is not None:
            adv = clamp((a["nps"] - 5) / 5, -1, 1)
            a["advocacy"] = round(50 + 45 * adv - 25 * (a["esc_ratio"] or 0), 1)
        elif a["sent_avg"] is not None:
            a["advocacy"] = round(50 + 40 * a["sent_avg"] - 25 * (a["esc_ratio"] or 0), 1)
        else:
            a["advocacy"] = 45.0
        a["advocacy"] = round(clamp(a["advocacy"], 0, 100), 1)

        a["renewal_risk"] = renew_risk(a, cfg)
        a["peer_p75"] = p75
        a["tier_target"] = peers["tier_median"].get(a["value_tier"], p75)   # 同层中位 = 向上销售目标
        a["peer_products"] = [p for p in PRODUCT_UNIVERSE if pen(p) >= 0.5]


_EVIDENCE_BY_TYPE = {
    "ACQUIRE": "推断",
    "WINBACK": "事实",
    "RENEW_DEFEND": "事实",
    "XSELL": "假设",
    "UP_CAPACITY": "推断",
    "UP_ARPU": "推断",
    "WHITESPACE": "假设",
}
_EVIDENCE_NOTE = {
    "事实": "直接来自系统记录的合同/交易/流失数据",
    "推断": "事实基础上的合理外推，需销售验证",
    "假设": "行业规律推导的可能性，必须先验证再投入",
}


def evidence_of(otype, a, score):
    """证据等级：事实 / 推断 / 假设 —— 让读者知道哪句话可以直接用，哪句要先去验证。"""
    lvl = _EVIDENCE_BY_TYPE.get(otype, "推断")
    if otype == "RENEW_DEFEND" and a.get("contract_end_estimated"):
        lvl = "推断"                      # 到期日是按签约周年估的，不能当事实
    if otype == "UP_ARPU" and (a["rev_window"] or 0) <= 0:
        lvl = "假设"
    if lvl == "事实" and score >= 65:
        conf = "高"
    elif lvl == "事实" or score >= 70:
        conf = "中"
    else:
        conf = "低"
    return lvl, conf


def gen_opportunities(accounts, peers, cfg):
    """为每个客户生成候选机会，按四维加权打分并排序（公式见 references/opportunity-model.md）。"""
    w = cfg_get(cfg, "opp_weights")
    win_rate = float(cfg_get(cfg, "win_rate"))
    penalty = float(cfg_get(cfg, "opp_defend_penalty"))
    win = max(1, peers["window"])
    opps = []

    def pen(p, ind):
        return peers["pen"].get((ind, p)) or peers["pen"].get(("__all__", p), 0.0)

    def pmed(p, ind):
        return peers["median"].get((ind, p)) or peers["median"].get(("__all__", p), 0.0)

    def dims_of(a, cross_sell=None, up_sell=None, white_space=None, advocacy=None):
        return {
            "cross_sell": round(clamp(a["cross_sell"] if cross_sell is None else cross_sell, 0, 100), 1),
            "up_sell": round(clamp(a["up_sell"] if up_sell is None else up_sell, 0, 100), 1),
            "white_space": round(clamp(a["white_space"] if white_space is None else white_space, 0, 100), 1),
            "advocacy": round(clamp(a["advocacy"] if advocacy is None else advocacy, 0, 100), 1),
        }

    def add(a, otype, label, product, amount, amount_type, driver, evidence, next_action,
            window_days, dims, base_score=None):
        if base_score is None:
            growth = (w["cross_sell"] * dims["cross_sell"] + w["up_sell"] * dims["up_sell"]
                      + w["white_space"] * dims["white_space"] + w["advocacy"] * dims["advocacy"])
            score = growth * (1 - penalty * a["renewal_risk"] / 100.0)
        else:
            score = base_score
        ev, conf = evidence_of(otype, a, score)
        opps.append({
            "account_id": a["account_id"], "account_name": a["account_name"],
            "tier": a["value_tier"], "industry": a["industry"], "region": a["region"],
            "owner": a["owner"], "opp_type": otype, "opp_type_label": label,
            "product_line": product, "est_amount": round(max(0.0, amount), 2),
            "amount_type": amount_type, "driver": driver, "evidence": evidence,
            "next_action": next_action, "window_days": int(window_days),
            "renewal_risk": a["renewal_risk"], "dims": dims, "score_raw": round(score, 1),
            "evidence_level": ev, "confidence": conf,
        })

    for a in accounts.values():
        if a.get("_no_master"):
            continue
        ind = a["industry"]

        # 0) 潜在客户：首单转化（不参与扩张折扣，避免被 0 消费拉低）
        if a["lifecycle"] == "潜在客户":
            top = a["peer_products"][0] if a["peer_products"] else "计算"
            add(a, "ACQUIRE", "首单转化", top, pmed(top, ind) * win_rate, "预估首单",
                "有活跃/试用但窗口内无付费，同行业客户已有首单先例",
                f"近3月试用 {int(a['trials_3m'])} 次、月活 {a['mau_avg_3m']:.0f}，窗口消费为 0",
                "锁定真实试用场景，7 天内给出首单方案与迁移支持", 30,
                dims_of(a), base_score=clamp(45 + 35 * min(1.0, a["trials_3m"] / 3.0)
                                             + 10 * a["advocacy"] / 100.0, 0, 90))
            continue

        # 1) 已流失：赢回
        if a["is_churned"] or a["lifecycle"] == "已流失":
            add(a, "WINBACK", "流失赢回", a["coverage"][0] if a["coverage"] else "全品类",
                a["lost_amount"] or a["rev_prev"] * 0.5, "预估赢回",
                f"已流失：{a['churn_reason'] or '原因未记录'}"
                + (f"；流向 {','.join(a['competitor_names'])}" if a["competitor_names"] else ""),
                f"流失前窗口消费 {fmt_money(a['rev_prev'])}"
                + (f"；流失金额 {fmt_money(a['lost_amount'])}" if a["lost_amount"] else ""),
                "竞品方案对比 + 回归专项报价，一把手层面对话", 60, dims_of(a),
                base_score=60.0 + {"S": 25, "A": 20, "B": 10, "C": 0}.get(a["value_tier"], 0))
            continue

        # 2) 续约保卫（有到期信息且风险达标才生成，避免噪声）
        if a["days_to_renewal"] is not None \
                and a["days_to_renewal"] <= int(cfg_get(cfg, "renewal_watch_days")) \
                and a["renewal_risk"] >= 45:
            at_risk = a["rev_window"] * a["renewal_risk"] / 100.0
            drv = f"合同 {a['days_to_renewal']} 天后到期，流失风险分 {a['renewal_risk']:.0f}"
            if a["competitor_strength"]:
                drv += f"，竞品信号强度 {a['competitor_strength']:.0%}"
            add(a, "RENEW_DEFEND", "续约保卫", "全品类", at_risk, "风险敞口", drv,
                f"到期日 {a['contract_end'] or '（按签约周年估算）'}；近3月负面占比 {(a['neg_ratio'] or 0):.0%}；"
                f"月活环比 {(pct_change(a['mau_avg_3m'], a['mau_prev_avg']) if a['mau_prev_avg'] else 0):+.0%}",
                "30 天内完成续约体检：用量盘点 + 竞品对比 + 商务方案，争取续约意向书",
                min(30, max(14, int((a["days_to_renewal"] or 30) * 0.5))),
                dims_of(a), base_score=a["renewal_risk"])

        # 3) 交叉销售
        missing = sorted([p for p in PRODUCT_UNIVERSE if p not in set(a["coverage"])],
                         key=lambda p: -pen(p, ind))
        for p in missing[:2]:
            pn = pen(p, ind)
            if pn < 0.30:
                continue
            amt = pmed(p, ind) * win_rate
            add(a, "XSELL", "交叉销售", p, amt, "预估年化",
                f"同行业 {pn:.0%} 的客户已采购{p}，该客户尚未覆盖"
                f"（已覆盖 {len(a['coverage'])}/{len(PRODUCT_UNIVERSE)} 个品类）",
                f"同行业{p}客户年中位消费 {fmt_money(pmed(p, ind))}；现有品类：{'、'.join(a['coverage'][:5]) or '无'}",
                f"以行业标准方案切入，安排 {p} 场景化 POC", 60,
                dims_of(a, cross_sell=a["cross_sell"] * (0.6 + 0.4 * pn)))

        # 4) 向上销售：容量压力
        if (a["usage_avg_3m"] or 0) >= 0.75 or (a["res_growth"] or 0) > 0.20:
            top_p = max(a["prod_rev"], key=a["prod_rev"].get) if a["prod_rev"] else "计算"
            amt = a["prod_rev"].get(top_p, 0) * 0.30
            add(a, "UP_CAPACITY", "容量扩容", top_p, amt, "预估年化",
                f"配额使用率 {(a['usage_avg_3m'] or 0):.0%}，资源数环比 {(a['res_growth'] or 0):+.0%}，存在扩容/升级压力",
                f"近3月新增资源 {int(a['new_res_3m'])} 个；{top_p} 窗口消费 {fmt_money(a['prod_rev'].get(top_p, 0))}",
                "提前 30 天出扩容/规格升级报价，把限流风险变成增购", 30,
                dims_of(a, up_sell=a["up_sell"] + 20))

        # 5) 向上销售：ARPU 差距（对标同层中位，且增幅不超过现有体量的 50%）
        target = a["tier_target"]
        if a["rev_window"] > 0 and target > 0 and a["rev_window"] < target * 0.75:
            gap_amt = min((target - a["rev_window"]) * 0.5, a["rev_window"] * 0.5)
            add(a, "UP_ARPU", "用量提升", a["coverage"][0] if a["coverage"] else "计算", gap_amt, "预估年化",
                f"窗口消费仅为同层中位的 {a['rev_window'] / target:.0%}，用量天花板未打开",
                f"同层中位窗口消费 {fmt_money(target)}，缺口 {fmt_money(target - a['rev_window'])}",
                "找出第二个业务场景横向复制，用承诺消费换阶梯价格", 180, dims_of(a))

        # 6) 空白市场 / 场景型
        for p in a["ws_products"][:1]:
            pn = pen(p, ind)
            amt = pmed(p, ind) * win_rate * 0.6
            add(a, "WHITESPACE", "场景空白", p, amt, "预估年化",
                f"{ind}{p}渗透率仅 {pn:.0%}，属高潜未饱和场景，且客户已具备落地前置条件",
                f"已购品类 {'、'.join(a['coverage'][:4]) or '无'}；同行业{p}中位消费 {fmt_money(pmed(p, ind))}",
                f"联合行业解决方案团队做 {p} 场景共创，先小规模验证再放量", 90,
                dims_of(a, white_space=a["white_space"] + 15))

    cut = cfg_get(cfg, "opp_priority_cut")
    for o in opps:
        o["priority"] = "A" if o["score_raw"] >= cut["A"] else ("B" if o["score_raw"] >= cut["B"] else "C")
        o["score"] = round(o["score_raw"], 1)
    opps.sort(key=lambda o: (-o["score"], -o["est_amount"]))

    top_n = int(cfg_get(cfg, "top_opps"))
    kept, seen = [], set()
    for o in opps:                                  # 先保证每个 S/A 客户至少 1 条
        if o["tier"] in ("S", "A") and o["account_id"] not in seen and len(kept) < top_n:
            kept.append(o)
            seen.add(o["account_id"])
    for o in opps:
        if len(kept) >= top_n:
            break
        if o not in kept:
            kept.append(o)
    kept.sort(key=lambda o: (-o["score"], -o["est_amount"]))
    for i, o in enumerate(kept, 1):
        o["opp_id"] = f"OPP-{i:03d}-{o['account_id']}"
    return kept


# ==================================================================== 预警

def gen_alerts(accounts, ctx, cfg):
    months = ctx["months"]
    z_cut = float(cfg_get(cfg, "alert_z_threshold"))
    alerts = []
    for a in accounts.values():
        if a["is_churned"]:
            continue                      # 已流失客户由 WINBACK 机会承接，不再重复报警
        rev_seq = [a["rev_month"].get(m, 0) for m in months]
        if not any(rev_seq):
            continue
        last = rev_seq[-1]
        prev = rev_seq[-2] if len(rev_seq) > 1 else 0
        mom = pct_change(last, prev)
        base6 = rev_seq[-7:-1] or rev_seq[:-1]
        z = 0.0
        if len(base6) >= 3:
            mu = mean(base6)
            sd = stdev(base6)
            z = (last - mu) / sd if sd > 0 else 0.0
        yoy = None
        if a["rev_prev"] > 0:
            yoy = safe_div(a["rev_window"], a["rev_prev"]) - 1

        def push(level, atype, metric, cur, base, delta, evidence, action):
            alerts.append({
                "account_id": a["account_id"], "account_name": a["account_name"],
                "tier": a["value_tier"], "lifecycle": a["lifecycle"], "owner": a["owner"],
                "level": level, "alert_type": atype, "metric": metric,
                "current": round(cur, 2) if isinstance(cur, (int, float)) else cur,
                "baseline": round(base, 2) if isinstance(base, (int, float)) else base,
                "delta": round(delta, 3) if isinstance(delta, (int, float)) else delta,
                "evidence": evidence, "action": action,
                "score": a["renewal_risk"] if atype == "LOSS" else a["up_sell"],
            })

        # 表格里放得下的趋势描述：只给首尾与均值，不铺 6 组原始数字
        mean6 = mean(base6) if base6 else 0.0
        seq_txt = f"近 6 月 {fmt_money(rev_seq[-6])} → {fmt_money(last)}"

        if mom is not None and mom <= -0.30 and last < mean(rev_seq) * 0.85:
            push("P0", "LOSS", "月消费断崖", last, prev, mom,
                 f"单月环比 {mom:+.0%}；近6月 {seq_txt}",
                 "48 小时内定位断崖原因（资源释放/项目结束/竞品替换），同步测算影响并给补救方案")
        if getattr(a, "slide_months", 0) >= 3 and (a.get("slide_cum") or 0) <= -0.15 \
                and a["tenure_months"] > 6:          # 爬坡期客户波动大，不判连续下滑
            push("P1", "LOSS", "连续下滑", last, mean(rev_seq), a["slide_cum"],
                 f"连续 {a['slide_months']} 个月下降，累计 {a['slide_cum']:+.0%}；{seq_txt}",
                 "做用量结构拆解，找出流失品类；对下降品类启动专项保留")
        if yoy is not None and yoy <= -0.20:
            push("P1", "LOSS", "同比下滑", a["rev_window"], a["rev_prev"], yoy,
                 f"窗口消费 {fmt_money(a['rev_window'])}，同比 {yoy:+.0%}",
                 "对比去年同期项目结构，识别一次性项目与结构性流失")
        if a["days_to_renewal"] is not None and a["days_to_renewal"] <= int(cfg_get(cfg, "renewal_urgent_days")) \
                and a["renewal_risk"] >= 55:
            push("P0", "LOSS", "续约高危", a["days_to_renewal"], int(cfg_get(cfg, "renewal_urgent_days")),
                 -a["days_to_renewal"] / 100.0,
                 f"{a['days_to_renewal']} 天后到期，风险分 {a['renewal_risk']}；负面占比 {(a['neg_ratio'] or 0):.0%}",
                 "启动高层续约对话，准备竞品对比与商务让步方案，锁定续约意向书")
        if a["mau_prev_avg"] and a["mau_avg_3m"] <= a["mau_prev_avg"] * 0.5 and a["mau_prev_avg"] > 0:
            push("P0", "LOSS", "活跃断流", a["mau_avg_3m"], a["mau_prev_avg"],
                 safe_div(a["mau_avg_3m"], a["mau_prev_avg"]) - 1,
                 f"月活由 {a['mau_prev_avg']:.0f} 降至 {a['mau_avg_3m']:.0f}",
                 "排查账号/权限/项目变更，48 小时内触达确认业务是否切走")
        if (a["neg_ratio"] or 0) >= 0.6 and a.get("recent_n", 0) >= 3:
            push("P1", "LOSS", "负面舆情抬头", a["neg_ratio"], 0.3, (a["neg_ratio"] or 0) - 0.3,
                 f"近3月负面反馈占比 {(a['neg_ratio'] or 0):.0%}（{a['feedback_n']} 条反馈）",
                 f"针对「{a['top_pain']}」出整改承诺与时间表，必要时服务级别升级")
        if abs(z) >= z_cut and last > 0:
            push("P2" if z > 0 else "P1", "GROWTH" if z > 0 else "LOSS", "统计异动",
                 last, mean(base6), z / 10,
                 f"较前 6 月均值 {fmt_money(mean6)} 偏离 {z:+.1f}σ；{seq_txt}",
                 "核实异动来源，属真实业务变化则更新预测与资源规划")
        if (a["usage_avg_3m"] or 0) >= 0.90:
            push("P1", "GROWTH", "配额饱和", a["usage_avg_3m"], 0.8, (a["usage_avg_3m"] or 0) - 0.8,
                 f"配额使用率 {(a['usage_avg_3m'] or 0):.0%}，存在限流风险",
                 "提前出扩容方案（叠加节省计划降单价），把风险变成增购")
        if yoy is not None and yoy >= 0.30 and (a["usage_avg_3m"] or 0) >= 0.6:
            push("P2", "GROWTH", "加速增长", yoy, 0.3, yoy - 0.3,
                 f"窗口消费同比 {yoy:+.0%}，使用率 {(a['usage_avg_3m'] or 0):.0%}",
                 "纳入重点增长名单，配置专属资源承接增量，同步谈包年锁定")
        if a["trials_3m"] >= 3 and a["rev_window"] <= 0:
            push("P2", "GROWTH", "试用激增", a["trials_3m"], 0, a["trials_3m"],
                 f"近3月试用 {int(a['trials_3m'])} 次但未产生付费",
                 "快速识别试用场景，48 小时内跟进转正方案")

    order = {"P0": 0, "P1": 1, "P2": 2}
    alerts.sort(key=lambda x: (order.get(x["level"], 3), -x["score"]))
    for i, al in enumerate(alerts, 1):
        al["alert_id"] = f"ALT-{i:03d}"
    return alerts


# ==================================================================== 汇总

def build_summary(accounts, pains, opps, alerts, ctx, quality, cfg):
    months = ctx["months"]
    live = [a for a in accounts.values() if not a.get("_no_master")]
    rev_now = sum(a["rev_window"] for a in accounts.values())
    rev_prev = sum(a["rev_prev"] for a in accounts.values())

    by_tier = {}
    for a in live:
        t = a["value_tier"]
        d = by_tier.setdefault(t, {"tier": t, "label": VALUE_TIER_LABEL[t], "count": 0, "revenue": 0.0,
                                   "opp_amount": 0.0, "alerts_p0": 0})
        d["count"] += 1
        d["revenue"] += a["rev_window"]
    for o in opps:
        if o["tier"] in by_tier:
            by_tier[o["tier"]]["opp_amount"] += o["est_amount"]
    for al in alerts:
        if al["level"] == "P0" and al["tier"] in by_tier:
            by_tier[al["tier"]]["alerts_p0"] += 1
    tiers = [by_tier[t] for t in VALUE_TIERS if t in by_tier]
    for t in tiers:
        t["rev_share"] = round(safe_div(t["revenue"], rev_now), 3)

    lc = {}
    for a in live:
        d = lc.setdefault(a["lifecycle"], {"stage": a["lifecycle"], "count": 0, "revenue": 0.0})
        d["count"] += 1
        d["revenue"] += a["rev_window"]
    lifecycles = sorted(lc.values(), key=lambda x: LIFECYCLE_ORDER.index(x["stage"])
                        if x["stage"] in LIFECYCLE_ORDER else 99)

    top_opps = opps[:10]
    matrix = []
    for a in live:
        if a["rev_window"] <= 0:
            continue
        matrix.append({"label": a["account_name"][:8], "x": a["rev_window"],
                       "y": a["growth"] or 0.0, "size": sum(o["est_amount"] for o in opps
                                                            if o["account_id"] == a["account_id"]),
                       "color": {"S": "#2b5b8c", "A": "#3f8ecc", "B": "#5eb0a8", "C": "#9aa5b1"}[a["value_tier"]]})

    rev_series = []
    for m in months:
        rev_series.append(sum(a["rev_month"].get(m, 0) for a in accounts.values()))

    total_opp = sum(o["est_amount"] for o in opps)

    # 价值 × 风险矩阵：给经营会看「哪块收入正在裸奔」
    risk_matrix = []
    for a in live:
        if a["rev_window"] <= 0:
            continue
        risk_matrix.append({
            "label": a["account_name"][:8], "x": a["rev_window"], "y": a["renewal_risk"],
            "size": sum(o["est_amount"] for o in opps if o["account_id"] == a["account_id"]),
            "color": {"S": "#2b5b8c", "A": "#3f8ecc", "B": "#5eb0a8", "C": "#9aa5b1"}[a["value_tier"]],
        })

    # 扩张就绪信号：不是「可能能卖」，而是有明确触发条件
    expansion = []
    for a in live:
        if a["lifecycle"] in ("已流失", "潜在客户") or a["renewal_risk"] >= 65:
            continue
        sig = []
        if (a["usage_avg_3m"] or 0) >= 0.85:
            sig.append(("配额饱和", f"配额使用率 {a['usage_avg_3m']:.0%}，已连续两月高位"))
        if len(a["coverage"]) <= 3 and a["rev_window"] > 0:
            sig.append(("品类稀疏", f"仅覆盖 {len(a['coverage'])}/12 个品类"))
        if (a["advocacy"] or 0) >= 70 and (a["nps"] or 0) >= 8:
            sig.append(("倡导者就绪", f"NPS {a['nps']:.0f}，可请求转介绍/案例共建"))
        if (a["res_growth"] or 0) >= 0.25:
            sig.append(("资源快增", f"资源数环比 {a['res_growth']:+.0%}，业务在放量"))
        if not sig:
            continue
        expansion.append({
            "account_id": a["account_id"], "account_name": a["account_name"],
            "tier": a["value_tier"], "risk": a["renewal_risk"],
            "signals": [x[0] for x in sig], "evidence": "；".join(x[1] for x in sig),
            "revenue": round(a["rev_window"], 2),
        })
    expansion.sort(key=lambda x: (-len(x["signals"]), -x["revenue"]))

    # 金额按类型分布用全量机会算，不能用 top10，否则与 KPI 的总口径对不上
    type_mix = {}
    for o in opps:
        type_mix[o["opp_type_label"]] = type_mix.get(o["opp_type_label"], 0) + o["est_amount"]

    # 证据构成：先告诉读者哪些结论可以直接用，哪些要先验证
    ev_mix = {}
    for o in opps:
        ev_mix[o["evidence_level"]] = ev_mix.get(o["evidence_level"], 0) + 1

    # 需要拍板的事：每条都带决策人、截止日、不决定的代价
    decisions = []
    p0_alerts = [x for x in alerts if x["level"] == "P0"]
    p0_pains = [p for p in pains if p["priority"] == "P0"]
    a_opps = [o for o in opps if o["priority"] == "A"]
    at_risk = round(sum(o["est_amount"] for o in opps if o["amount_type"] == "风险敞口"), 2)
    if p0_alerts or at_risk > 0:
        accs = len({x["account_id"] for x in p0_alerts}) or len({o["account_id"] for o in opps
                                                                if o["amount_type"] == "风险敞口"})
        decisions.append({
            "question": f"是否为 {accs} 家高危客户启动续约保卫专项（含商务让利额度）？",
            "why": f"P0 预警 {len(p0_alerts)} 条，风险敞口 {fmt_money(at_risk)}；"
                   f"这类客户一旦流失，同期新增机会难以补位",
            "owner": "客户成功/销售主管", "due": "7 天内",
            "cost": f"按 50% 流失概率计，约 {fmt_money(at_risk * 0.5)} 收入存在实质风险",
            "level": "P0",
        })
    if p0_pains:
        p = p0_pains[0]
        decisions.append({
            "question": f"是否为「{p['category']}」立项整改并给出路线图？",
            "why": f"{p['account_count']} 家客户提及，暴露金额 {fmt_money(p['exposure_amount'])}，"
                   f"且同类问题重复出现（{p['mentions']} 条）",
            "owner": "产品/服务负责人", "due": "30 天内给答复",
            "cost": f"覆盖 {fmt_money(p['exposure_amount'])} 收入基础，且是续约谈判中的直接减分项",
            "level": "P1",
        })
    growth_amt = round(sum(o["est_amount"] for o in opps if o["amount_type"] != "风险敞口"), 2)
    if a_opps:
        names = "、".join(o["account_name"][:8] for o in a_opps[:3])
        decisions.append({
            "question": f"是否给 {names} 追加售前/解决方案资源，把 A 级机会推进到商务阶段？",
            "why": f"A 级机会 {len(a_opps)} 条，增长型预估增量 {fmt_money(growth_amt)}，"
                   f"当前卡点在方案与 POC 资源，不在客户意愿",
            "owner": "销售主管 + 解决方案", "due": "本月内",
            "cost": "窗口期 60~90 天，错过则进入下一预算周期",
            "level": "P1",
        })
    missing = [x for x in (quality.get("issues") or []) if "缺少" in x]
    if missing:
        decisions.append({
            "question": "是否补齐缺失数据域，把结论强度从「方向性」提到「可考核」？",
            "why": "；".join(m[:28] for m in missing[:2]),
            "owner": "数据/IT 负责人", "due": "下个报告周期前",
            "cost": "缺口直接影响画像与机会金额的可信度，当前结论需人工复核",
            "level": "P2",
        })
    return {
        "brand": cfg.get("brand", {}),
        "meta": {"as_of": cfg["as_of"], "window_months": int(cfg_get(cfg, "window_months")),
                 "window": [months[0], months[-1]], "generated_at": cfg.get("_generated_at"),
                 "tier_percentile": cfg_get(cfg, "tier_percentile"),
                 "opp_weights": cfg_get(cfg, "opp_weights")},
        "kpi": {
            "accounts": len(live), "na_accounts": sum(1 for a in live if a["na_flag"]),
            "opp_amount_growth": round(sum(o["est_amount"] for o in opps
                                           if o["amount_type"] not in ("风险敞口",)), 2),
            "opp_amount_defend": round(sum(o["est_amount"] for o in opps
                                           if o["amount_type"] == "风险敞口"), 2),
            "revenue_window": round(rev_now, 2), "revenue_prev": round(rev_prev, 2),
            "growth": round(safe_div(rev_now, rev_prev) - 1, 4) if rev_prev else None,
            "opp_count": len(opps), "opp_amount": round(total_opp, 2),
            "opp_a": sum(1 for o in opps if o["priority"] == "A"),
            "at_risk_amount": round(sum(o["est_amount"] for o in opps if o["opp_type"] == "RENEW_DEFEND"), 2),
            "alerts_p0": sum(1 for a in alerts if a["level"] == "P0"),
            "alerts_p1": sum(1 for a in alerts if a["level"] == "P1"),
            "pain_p0": sum(1 for p in pains if p["priority"] == "P0"),
            "churn_risk_accounts": sum(1 for a in live if a["lifecycle"] == "流失预警"),
            "avg_activity": round(mean([a["activity_score"] for a in live if a["activity_score"] is not None]) or 0, 3),
        },
        "tiers": tiers,
        "lifecycles": lifecycles,
        "matrix": matrix,
        "risk_matrix": risk_matrix,
        "expansion": expansion[:8],
        "evidence_mix": ev_mix,
        "opp_type_mix": [{"label": k, "value": round(v, 2)}
                         for k, v in sorted(type_mix.items(), key=lambda x: -x[1])],
        "decisions": decisions,
        "revenue_series": {"labels": months, "values": rev_series},
        "pains": pains[:8],
        "alerts": alerts[:15],
        "opps": top_opps,
        "data_quality": quality,
        "accounts": [{
            "account_id": a["account_id"], "account_name": a["account_name"], "tier": a["value_tier"],
            "lifecycle": a["lifecycle"], "activity": a["activity_level"],
            "persona": a["persona"], "revenue": round(a["rev_window"], 2),
            "growth": round(a["growth"], 4) if a["growth"] is not None else None,
            "renewal_risk": a["renewal_risk"], "owner": a["owner"], "industry": a["industry"],
            "pain": a["top_pain"], "coverage": a["coverage"], "opp_amount": round(
                sum(o["est_amount"] for o in opps if o["account_id"] == a["account_id"]), 2),
            "days_to_renewal": a["days_to_renewal"],
            "actions": a["actions"][:2], "strategy": a["strategy"], "kpi": a["kpi"],
            "root_cause": a.get("root_cause", ""), "usage": a["usage_avg_3m"],
            "coverage_n": len(a["coverage"]), "nps": a["nps"],
        } for a in sorted(live, key=lambda x: -x["rev_window"])],
    }


def write_outputs(accounts, pains, opps, alerts, summary, out_dir):
    ensure_dir(out_dir)
    prof_headers = ["account_id", "account_name", "industry", "region", "owner", "na_flag",
                    "value_tier", "value_pct", "value_tier_label", "lifecycle", "activity_level",
                    "activity_score", "persona", "rfm", "rev_window", "rev_prev", "growth", "growth_basis",
                    "active_months", "recency_months", "mau_avg_3m", "usage_avg_3m", "resource_count",
                    "ticket_3m", "sent_avg", "neg_ratio", "nps", "competitor_names", "competitor_strength",
                    "days_to_renewal", "renewal_risk", "cross_sell", "up_sell", "white_space", "advocacy",
                    "coverage", "top_pain", "root_cause", "strategy", "actions", "kpi", "cadence"]
    rows = []
    for a in accounts.values():
        if a.get("_no_master"):
            continue
        r = {k: a.get(k) for k in prof_headers}
        r["rfm"] = f"R{a['rfm']['R']}/F{a['rfm']['F']}/M{a['rfm']['M']}"
        r["actions"] = " | ".join(a.get("actions", []))
        r["coverage"] = "、".join(a.get("coverage", []))
        r["competitor_names"] = "、".join(a.get("competitor_names", []))
        r["growth"] = round(a["growth"], 4) if a.get("growth") is not None else None
        for k in ("rev_window", "rev_prev"):
            r[k] = round(a.get(k) or 0, 2)
        rows.append(r)
    write_csv(os.path.join(out_dir, "account_profile.csv"), prof_headers, rows)

    pain_headers = ["priority", "priority_score", "impact_rank", "volume_rank", "rank_gap",
                    "category", "mentions", "account_count", "breadth",
                    "intensity", "severity", "exposure_amount", "dominant_tier", "escalated_ratio",
                    "is_common", "sources", "top_quote", "suggested_action", "sample_accounts"]
    write_csv(os.path.join(out_dir, "pain_points.csv"), pain_headers,
              [{k: p.get(k) for k in pain_headers} for p in pains])

    opp_headers = ["opp_id", "priority", "score", "account_id", "account_name", "tier", "industry",
                   "region", "owner", "opp_type", "opp_type_label", "product_line", "est_amount",
                   "amount_type", "window_days", "renewal_risk", "evidence_level", "confidence",
                   "driver", "evidence", "next_action"]
    orows = []
    for o in opps:
        r = {k: o.get(k) for k in opp_headers}
        r["est_amount"] = round(o["est_amount"], 2)
        orows.append(r)
    write_csv(os.path.join(out_dir, "opportunity_list.csv"), opp_headers, orows)

    al_headers = ["alert_id", "level", "alert_type", "account_id", "account_name", "tier", "lifecycle",
                  "owner", "metric", "current", "baseline", "delta", "evidence", "action"]
    write_csv(os.path.join(out_dir, "alerts.csv"), al_headers,
              [{k: al.get(k) for k in al_headers} for al in alerts])

    write_json(os.path.join(out_dir, "summary.json"), summary)


def archive_snapshot(out_dir, summary, accounts, pains, alerts):
    """把本期结果压缩存档，供下一期做"上期动作是否见效"的闭环对比。

    只存对比需要的字段，单期文件几十 KB，默认保留最近 24 期。
    """
    hist = os.path.join(out_dir, "history")
    ensure_dir(hist)
    snap = {
        "as_of": summary["meta"]["as_of"],
        "generated_at": summary["meta"].get("generated_at"),
        "kpi": {k: summary["kpi"].get(k) for k in
                ("accounts", "revenue_window", "growth", "alerts_p0", "churn_risk_accounts",
                 "pain_p0", "opp_amount_growth", "opp_amount_defend", "avg_activity", "opp_a")},
        "accounts": [{"account_id": a["account_id"], "account_name": a["account_name"],
                      "revenue": round(a["rev_window"], 2), "risk": a["renewal_risk"],
                      "lifecycle": a["lifecycle"], "tier": a["value_tier"]}
                     for a in accounts.values() if not a.get("_no_master")],
        "pains": [{"category": p["category"], "priority": p["priority"],
                   "score": p["priority_score"], "impact_rank": p["impact_rank"]} for p in pains],
        "p0_accounts": sorted({al["account_id"] for al in alerts if al["level"] == "P0"}),
        "watch_accounts": sorted({a["account_id"] for a in accounts.values()
                                  if a["lifecycle"] == "流失预警"}),
    }
    path = os.path.join(hist, f"{summary['meta']['as_of']}.json")
    write_json(path, snap)
    files = sorted(f for f in os.listdir(hist) if f.endswith(".json"))
    for old in files[:-24]:
        try:
            os.remove(os.path.join(hist, old))
        except OSError:
            pass
    return path


def run(interim_dir, out_dir=None, config_path=None):
    rt_path = os.path.join(interim_dir, "runtime_config.json")
    cfg = read_json(rt_path) if os.path.exists(rt_path) else read_json(config_path)
    out_dir = out_dir or os.path.dirname(os.path.abspath(interim_dir.rstrip("/\\"))) + "/analysis"
    ensure_dir(out_dir)

    import datetime
    cfg["_generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    eprint("→ 加载标准表")
    tables = load_tables(interim_dir)
    for k, v in tables.items():
        eprint(f"  {k}: {len(v)} 行")

    eprint("→ 计算客户指标")
    accounts, ctx = build_metrics(tables, cfg)
    total_rev = sum(a["rev_window"] for a in accounts.values())
    if total_rev <= 0:
        eprint("  ⚠ 全部客户窗口消费为 0：请确认 transactions 是否接入、金额字段是否识别、账期是否落在窗口内")
        eprint("    继续执行，但分层/机会金额将无意义，仅作流程连通性验证")
    assign_tiers(accounts, cfg)
    assign_activity(accounts, cfg)
    assign_lifecycle(accounts, cfg, ctx)
    assign_playbook(accounts)

    eprint("→ 痛点挖掘")
    pains = analyze_pains(tables, accounts, ctx, cfg)
    eprint(f"  {len(pains)} 类痛点，P0 {sum(1 for p in pains if p['priority'] == 'P0')} 个")

    eprint("→ 机会点识别")
    peers = peer_stats(accounts, ctx)
    compute_dimensions(accounts, peers, cfg)
    opps = gen_opportunities(accounts, peers, cfg)
    eprint(f"  {len(opps)} 条机会，A 级 {sum(1 for o in opps if o['priority'] == 'A')} 条")

    eprint("→ 趋势预警")
    alerts = gen_alerts(accounts, ctx, cfg)
    eprint(f"  {len(alerts)} 条预警，P0 {sum(1 for a in alerts if a['level'] == 'P0')} 条")

    q_path = os.path.join(interim_dir, "data_quality.json")
    quality = read_json(q_path) if os.path.exists(q_path) else {}
    summary = build_summary(accounts, pains, opps, alerts, ctx, quality, cfg)
    write_outputs(accounts, pains, opps, alerts, summary, out_dir)
    try:
        archive_snapshot(out_dir, summary, accounts, pains, alerts)
    except Exception as e:                       # 快照失败不应阻断主流程
        eprint(f"  ⚠ 历史快照写入失败（不影响本期报告）：{e}")
    eprint(f"✓ 分析完成 → {out_dir}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Step2 分层画像/痛点/机会/预警")
    ap.add_argument("--interim", default="./insight-output/interim")
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default=None, help="interim 缺 runtime_config.json 时使用")
    a = ap.parse_args()
    run(a.interim, a.out, a.config)


if __name__ == "__main__":
    main()
