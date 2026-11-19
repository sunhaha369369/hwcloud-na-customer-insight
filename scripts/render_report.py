#!/usr/bin/env python
"""Step 3 · 生成图文洞察报告（单文件 HTML，图表为内联 SVG，无外部依赖）。

    python scripts/render_report.py --analysis ./insight-output/analysis
    python scripts/render_report.py --analysis ./insight-output/analysis --out ./报告.html

报告结构遵循「决策优先」：先给判断和要拍板的事，再给证据，最后给明细。
输出：report.html（可直接浏览器打开 / 打印为 PDF / 贴进汇报材料）
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import svgchart as ch
from lib.common import ensure_dir, fmt_money, fmt_pct, read_json
from lib.svgchart import esc

TEMPLATE_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "report_template.html"),
]
EVIDENCE_CLS = {"事实": "fact", "推断": "infer", "假设": "assume"}


# ------------------------------------------------------------------ 小组件

def table(headers, rows, cls="", widths=None):
    if not rows:
        return '<p class="empty">（本节无数据）</p>'
    ths, cells = [], []
    for i, h in enumerate(headers):
        style = f' style="width:{widths[i]}"' if widths and i < len(widths) else ""
        ths.append(f"<th{style}>{esc(h)}</th>")
    body = []
    for r in rows:
        tds = []
        for i, c in enumerate(r):
            style = f' style="width:{widths[i]}"' if widths and i < len(widths) else ""
            tds.append(f"<td{style}>{c}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<table class="{cls}"><thead><tr>{"".join(ths)}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def badge(text, kind="n"):
    return f'<span class="bdg bdg-{kind}">{esc(text)}</span>'


def lvl_badge(level):
    return badge(level, {"P0": "p0", "P1": "p1", "P2": "p2"}.get(level, "eq"))


def pr_badge(level):
    return badge(level + "级", {"A": "p0", "B": "p1", "C": "p2"}.get(level, "eq"))


def tier_badge(tier, label=""):
    return f'<span class="bdg bdg-t{tier}">{esc(label or tier)}</span>'


def ev_badge(level, conf=""):
    s = f'<span class="bdg bdg-{EVIDENCE_CLS.get(level, "eq")}">{esc(level)}</span>'
    if conf:
        s += f'<span class="sub"> · {esc(conf)}</span>'
    return s


def card(title, body, note=""):
    n = f'<div class="card-note">{note}</div>' if note else ""
    return f'<div class="card"><h4>{esc(title)}</h4>{body}{n}</div>'


def money_cell(v, kind=""):
    s = fmt_money(v)
    cls = {"defend": "amt-defend", "growth": "amt-growth"}.get(kind, "")
    return f'<span class="{cls}">{s}</span>'


def delta_html(cur, prev, invert=False, fmt="pct", eps=0.0):
    """与上期比的变化，返回带颜色的小字。"""
    if prev is None or cur is None:
        return ""
    if fmt == "pct":
        d = (cur - prev)
        pct = (cur / prev - 1) if prev else None
        txt = f"{pct:+.0%}" if pct is not None else "新增"
    else:
        d = cur - prev
        txt = f"{d:+.0f}"
    if abs(d) <= eps:
        return f'<span class="delta flat">持平</span>'
    good = (d < 0) if invert else (d > 0)
    return f'<span class="delta {"good" if good else "bad"}">较上期 {txt}</span>'


# ------------------------------------------------------------------ 一句话结论

def verdict(s, base):
    k = s["kpi"]
    tiers = s["tiers"]
    risk_accs = [a for a in s["accounts"] if a["lifecycle"] == "流失预警"]
    risk_rev = sum(a["revenue"] for a in risk_accs)
    growth_amt = k.get("opp_amount_growth", 0)
    defend_amt = k.get("opp_amount_defend", 0)
    growth = k.get("growth")

    sa_share = sum(t["rev_share"] for t in tiers if t["tier"] in ("S", "A"))
    head = f"{k['accounts']} 家客户窗口消费 <b>{fmt_money(k['revenue_window'])}</b>"
    head += f"，同比 <b>{fmt_pct(growth)}</b>" if growth is not None else "（同比口径不足）"
    head += f"；S/A 两层贡献 {fmt_pct(sa_share)} 收入。"

    if risk_accs:
        names = "、".join(a["account_name"][:10] for a in risk_accs[:2])
        body = (f"但 <em>{len(risk_accs)} 家客户已进入流失预警</em>（{esc(names)}），"
                f"对应年化 {fmt_money(risk_rev)}、风险敞口 {fmt_money(defend_amt)}")
        if growth_amt and defend_amt >= growth_amt * 0.5:
            body += (f"，与同期 {fmt_money(growth_amt)} 的增长机会相比，"
                     f"<em>保盘子的优先级高于谈增量</em>。")
        else:
            body += f"；同期可挖掘增长机会 {fmt_money(growth_amt)}。"
    elif growth_amt:
        body = (f"未见流失预警客户，本期重心在扩张：可挖掘增长机会 "
                f"<b>{fmt_money(growth_amt)}</b>，其中 A 级 {k['opp_a']} 条。")
    else:
        body = "本期未识别到明显风险或增量机会，建议先复核数据口径与客户范围。"

    sub = (f"建议排产顺序：先处理 {k['alerts_p0']} 条 P0 预警，再推进 A 级机会，"
           f"最后做共性痛点治理。本页第 00 节列出需要拍板的 {len(s.get('decisions') or [])} 项事项。")
    return (f'<div class="verdict"><div class="verdict-k">本期结论</div>'
            f'<div class="verdict-t">{head}{body}</div>'
            f'<div class="verdict-s">{sub}</div></div>')


# ------------------------------------------------------------------ 摘要叙述

def narrative(s):
    k = s["kpi"]
    meta = s["meta"]
    pains = s["pains"]
    alerts = s["alerts"]
    opps = s["opps"]

    lines = []
    p0_alerts = [a for a in alerts if a["level"] == "P0"]
    p0_pains = [p for p in pains if p["priority"] == "P0"]
    a_opps = [o for o in opps if o["priority"] == "A"]

    if p0_pains:
        p = p0_pains[0]
        lines.append(
            f"<b>需求面</b>：共性痛点首位是 <b>{esc(p['category'])}</b>——{p['account_count']} 家客户提及、"
            f"暴露 {fmt_money(p['exposure_amount'])}，在「声量排名 {p['volume_rank']} / 影响排名 "
            f"{p['impact_rank']}」中位列{'双榜第一' if p['volume_rank'] == p['impact_rank'] == 1 else '前列'}。"
            f"原话：<i>“{esc(p['top_quote'][:56])}”</i>"
        )
    if p0_alerts:
        top = p0_alerts[0]
        lines.append(
            f"<b>风险面</b>：P0 预警 {k['alerts_p0']} 条，最紧急的是 <b>{esc(top['account_name'][:12])}</b>"
            f"（{esc(top['metric'])}）：{esc(top['evidence'][:52])}。"
        )
    if a_opps:
        o = a_opps[0]
        lines.append(
            f"<b>机会面</b>：{k['opp_count']} 条机会中 A 级 {k['opp_a']} 条；排序最高的是 "
            f"<b>{esc(o['account_name'][:12])}</b> 的{esc(o['opp_type_label'])}"
            f"（{fmt_money(o['est_amount'])}，证据等级：{o['evidence_level']}）。"
        )
    if s.get("expansion"):
        e = s["expansion"][0]
        lines.append(
            f"<b>扩张面</b>：{len(s['expansion'])} 家客户出现明确扩张信号，最典型的是 "
            f"<b>{esc(e['account_name'][:12])}</b>（{'、'.join(e['signals'])}），"
            f"属「有触发条件」而非「凭感觉」的加码对象。"
        )
    if not lines:
        lines.append(f"本期覆盖 {k['accounts']} 家客户，窗口消费 {fmt_money(k['revenue_window'])}，"
                     f"未触发 P0 预警与 P0 痛点。")
    lines.append(f'<span class="src">口径：{meta["window"][0]}～{meta["window"][1]}，'
                 f'金额为不含税账单金额；估算字段已逐条标注证据等级。</span>')
    return "".join(f"<p>{x}</p>" for x in lines)


def decisions_html(s):
    ds = s.get("decisions") or []
    if not ds:
        return '<p class="ok">本期无需上报决策事项。</p>'
    rows = []
    for i, d in enumerate(ds, 1):
        lv = "lv0" if d["level"] == "P0" else ""
        q = (f'<div class="dec-q">{lvl_badge(d["level"])}<span>{esc(d["question"])}</span></div>')
        rows.append(
            f'<div class="dec-row {lv}">'
            f'<div class="dec-n">{i}</div>'
            f'<div class="dec-body">{q}'
            f'<p class="dec-why">{esc(d["why"])}</p>'
            f'<p class="dec-cost">{esc(d["cost"])}</p></div>'
            f'<div class="dec-meta"><span>决策人</span><b>{esc(d["owner"])}</b>'
            f'<span>建议时点</span><b>{esc(d["due"])}</b></div>'
            f"</div>"
        )
    return '<div class="dec">' + "".join(rows) + "</div>"


def must_do(s):
    """本周必做：同一客户只出一条（P0 预警优先，其次 A 级机会），再加 P0 共性痛点。"""
    items, used_acc = [], set()
    for a in s["alerts"]:
        if a["level"] == "P0" and a["account_id"] not in used_acc:
            used_acc.add(a["account_id"])
            items.append(f'<b>{esc(a["account_name"][:14])}</b>｜{esc(a["metric"])}：{esc(a["action"])}')
    for o in s["opps"]:
        if o["priority"] == "A" and o["account_id"] not in used_acc:
            used_acc.add(o["account_id"])
            items.append(f'<b>{esc(o["account_name"][:14])}</b>｜{esc(o["opp_type_label"])}'
                         f'（{fmt_money(o["est_amount"])}）：{esc(o["next_action"])}')
    for o in s["opps"]:
        if len(items) >= 4:
            break
        if o["priority"] == "A" and o["opp_type"] != "RENEW_DEFEND":
            items.append(f'<b>{esc(o["account_name"][:14])}</b>｜{esc(o["opp_type_label"])}'
                         f'（{fmt_money(o["est_amount"])}）：{esc(o["next_action"])}')
    for p in s["pains"]:
        if p["priority"] == "P0":
            items.append(f'共性痛点 <b>{esc(p["category"])}</b>：{esc(p["suggested_action"])}')
    return list(dict.fromkeys(items))[:5]


# ------------------------------------------------------------------ 各章节

def sec_data(s):
    q = s.get("data_quality") or {}
    tables = q.get("tables", {})
    rows = []
    labels = {"accounts": "客户主数据", "transactions": "交易/消费", "behavior": "行为活跃",
              "feedback": "反馈/工单/舆情", "churn": "流失记录", "competitor": "竞品动态",
              "opportunities": "历史商机"}
    for t, lab in labels.items():
        info = tables.get(t) or {}
        n = info.get("rows", 0)
        miss = info.get("required_missing") or []
        state = ("缺失" if n == 0 else
                 ("部分缺失：" + "、".join(f"{m['field']}({m['rate']:.0%})" for m in miss) if miss else "完整"))
        rows.append([lab, f'<span class="num">{n}</span>', state])
    issues = q.get("issues") or []
    issue_html = ("<ul class='issues'>" + "".join(f"<li>{esc(i)}</li>" for i in issues) + "</ul>") \
        if issues else "<p class='ok'>未发现阻断性数据质量问题。</p>"

    mix = s.get("evidence_mix") or {}
    order = ["事实", "推断", "假设"]
    colors = {"事实": "#2f7d32", "推断": "#e8a33d", "假设": "#b9c2cd"}
    mix_items = [{"label": f"{lv} {mix.get(lv, 0)} 条", "value": mix.get(lv, 0),
                  "color": colors[lv]} for lv in order if mix.get(lv)]
    mix_note = ("<b>事实</b>可直接进 CRM 跟进；<b>推断</b>需销售先确认再投入；"
                "<b>假设</b>必须先验证——这是本报告与「AI 生成的漂亮话」的区别。")
    meta = s["meta"]
    return f"""
<section id="s1">
  <h2><span class="num">01</span>数据与口径<span class="tag">先确认可信度，再看结论</span></h2>
  <p class="lead">分析窗口 <b>{meta['window'][0]} ～ {meta['window'][1]}</b>（{meta['window_months']} 个月），
  基准月 <b>{meta['as_of']}</b>。金额为不含税账单金额，按客户 × 月 × 产品线汇总。
  凡标注「窗口内趋势」的增速，表示同比数据不足 12 个月时改用窗口内趋势估算，结论强度较低。</p>
  <div class="grid-2">
    {card("输入数据完整性", table(["数据域", "记录数", "状态"], rows, "tbl", ["30%", "16%", "auto"]))}
    {card("机会证据构成", ch.hbar(mix_items, width=470, label_w=112, value_w=80,
                             fmt=lambda v: ""), mix_note)}
  </div>
  {card("数据质量提示", issue_html)}
</section>"""


def sec_decisions(s):
    must = "".join(f"<li>{x}</li>" for x in must_do(s)) or "<li class='muted'>无紧急事项</li>"
    return f"""
<section id="s0">
  <h2><span class="num">00</span>决策摘要<span class="tag">这一页给主管看</span></h2>
  <p class="lead">每一条都写明<b>为什么要现在决定</b>、<b>谁来决定</b>、<b>不决定会损失什么</b>。
  没有决策人的报告只是 newsletter。</p>
  {decisions_html(s)}
  <div class="must">
    <h4>本周必做（按紧急度排序，同一客户只出一条）</h4>
    <ol class="must-list">{must}</ol>
  </div>
  <h3>本期判断</h3>
  <div class="exec-text">{narrative(s)}</div>
</section>"""


def sec_seg(s, base):
    k = s["kpi"]
    tc = s["meta"].get("tier_percentile") or {}
    tiers = s["tiers"]
    tier_items = [{"label": f'{t["label"]}（{t["count"]}家）', "value": t["revenue"],
                   "color": ch.TIER_COLOR[t["tier"]]} for t in tiers]
    lc_items = [{"label": f'{l["stage"]}（{l["count"]}家）', "value": l["revenue"],
                 "color": ch.PALETTE[i % len(ch.PALETTE)]} for i, l in enumerate(s["lifecycles"])]

    revs = sorted(a["revenue"] for a in s["accounts"] if a["revenue"] > 0)
    med = revs[len(revs) // 2] if revs else 0
    matrix = ch.scatter(s.get("risk_matrix") or [], width=1000, height=360,
                        x_label="窗口消费（价值）", y_label="续约/流失风险分",
                        x_fmt=lambda v: fmt_money(v), y_fmt=lambda v: f"{v:.0f}",
                        split_x=med, split_y=50, y_range=(0, 100), bubble_scale=0.92,
                        quadrants=False)

    def q(a):
        return ("hr" if a["renewal_risk"] >= 50 else "lr") + ("_hv" if a["revenue"] >= med else "_lv")
    quads = {
        "hr_hv": ("高价值 · 高风险", "48 小时内高管介入，先算 3 个月干预成本 vs 收入损失", "warn"),
        "lr_hv": ("高价值 · 低风险", "扩张主战场，QBR 花在识别新场景而不是防守", "brand"),
        "hr_lv": ("低价值 · 高风险", "自动化触达 + 标准话术，不投入人力", "amber"),
        "lr_lv": ("低价值 · 低风险", "自助与渠道维护，只监控突变", "muted"),
    }
    qcount = {}
    for a in s["accounts"]:
        k2 = q(a)
        qcount[k2] = qcount.get(k2, 0) + 1
    qstrip = "".join(
        f'<div class="qcell {v[2]}"><div class="q-n">{qcount.get(k2, 0)} 家</div>'
        f'<div class="q-t">{esc(v[0])}</div><div class="q-a">{esc(v[1])}</div></div>'
        for k2, v in quads.items())
    hi_risk_hi_val = [a for a in s["accounts"] if a["renewal_risk"] >= 50 and a["revenue"] >= med]
    sa_share = sum(t["rev_share"] for t in tiers if t["tier"] in ("S", "A"))

    # 生命周期与活跃度合并成一列：10 列在 A4 宽度下会把「高活跃」挤成两行
    rows = []
    for a in s["accounts"][:12]:
        risk_cls = "risk-hi" if a["renewal_risk"] >= 60 else ""
        rows.append([
            f'<b>{esc(a["account_name"][:16])}</b>'
            f'<div class="sub">{esc(a["industry"])} · {esc(a["owner"])}</div>',
            tier_badge(a["tier"]),
            f'{esc(a["lifecycle"])}<div class="sub">{esc(a["activity"])}</div>',
            f'<div class="persona">{esc(a["persona"])}</div>',
            f'<span class="num">{fmt_money(a["revenue"])}</span>',
            f'<span class="num">{fmt_pct(a["growth"]) if a["growth"] is not None else "-"}</span>',
            f'<span class="num {risk_cls}">{a["renewal_risk"]:.0f}</span>',
            f'<span class="num">{money_cell(a["opp_amount"], "growth")}</span>',
            f'<div class="drv">{esc(a.get("root_cause", "")[:52])}</div>',
        ])
    return f"""
<section id="s2">
  <h2><span class="num">02</span>客户分层与画像<span class="tag">分得清，才知道先打谁</span></h2>
  <p class="lead">价值按窗口消费分位（S≥{fmt_pct(tc.get("S", 0.85), 0)}、A≥{fmt_pct(tc.get("A", 0.65), 0)}、
  B≥{fmt_pct(tc.get("B", 0.35), 0)}），活跃度按行为加权，生命周期按状态机判定——三者组合成画像标签，
  每个标签绑定一套动作、KPI 与触达节奏。
  <b>S/A 两层合计贡献 {fmt_pct(sa_share)} 收入</b>，因此它们的风险就是公司的风险。</p>
  {card("价值 × 风险矩阵（气泡＝该客户机会金额）", matrix + f'<div class="qstrip">{qstrip}</div>',
        f"横轴是窗口消费（价值），纵轴是续约/流失风险分。当前有 {len(hi_risk_hi_val)} 家高价值客户"
        "风险分 ≥ 50，属右上象限——对这一档做 3 个月干预的成本，远低于收入损失。")}
  <div class="grid-2">
    {card("各层级收入贡献", ch.hbar(tier_items, width=470, label_w=112, value_w=96))}
    {card("生命周期分布", ch.hbar(lc_items, width=470, label_w=112, value_w=96))}
  </div>
  <h3>客户画像与经营策略<span class="hint">「一句话根因」说明它为什么是这个状态，可直接用于周会</span></h3>
  {table(["客户", "层级", "生命周期 / 活跃度", "画像标签", "窗口消费", "增速", "续约风险", "机会金额", "一句话根因"],
         rows, "tbl", ["15%", "6%", "12%", "15%", "9%", "6%", "6%", "8%", "auto"])}
  <p class="foot">完整 12 项画像字段（RFM、品类覆盖、痛点标签、KPI 等）见 <code>account_profile.csv</code>。</p>
</section>"""


def sec_pain(s):
    pains = s["pains"]
    if not pains:
        return '<section id="s3"><h2><span class="num">03</span>需求与痛点</h2><p class="empty">无反馈数据。</p></section>'
    slope = ch.rank_slope(
        [{"label": p["category"], "left": p["volume_rank"], "right": p["impact_rank"]} for p in pains],
        width=1000, row_h=32, left_title="按提及条数排名", right_title="按加权影响排名")
    rows = []
    for p in pains:
        gap = p["rank_gap"]
        move = ('<div class="sub">↑ 影响高于声量</div>' if gap > 0
                else ('<div class="sub">↓ 声量高于影响</div>' if gap < 0 else ""))
        rows.append([
            lvl_badge(p["priority"]),
            f'<b>{esc(p["category"])}</b>' + ("" if p["is_common"] else '<span class="tag-single">个案</span>'),
            f'<span class="num">{p["account_count"]} 家</span><div class="sub">{p["mentions"]} 条</div>',
            f'<span class="num">{fmt_pct(p["breadth"], 0)}</span>',
            f'<span class="num">{money_cell(p["exposure_amount"])}</span>',
            f'<span class="num">{p["priority_score"]:.0f}</span>',
            f'<span class="num">{p["volume_rank"]} → {p["impact_rank"]}</span>{move}',
            f'<div class="quote">“{esc(p["top_quote"])}”</div>',
            f'<div class="act">{esc(p["suggested_action"])}</div>',
        ])
    common = [p for p in pains if p["is_common"]]
    biggest_gap = max(pains, key=lambda p: p["rank_gap"]) if pains else None
    gap_note = ""
    if biggest_gap and biggest_gap["rank_gap"] > 0:
        gap_note = (f"最值得注意的是 <b>{esc(biggest_gap['category'])}</b>：声量只排第 "
                    f"{biggest_gap['volume_rank']}，影响却排第 {biggest_gap['impact_rank']}——"
                    f"这类「不吵但贵」的问题最容易在优先级排序里被漏掉。")
    else:
        gap_note = "本期声量与影响排序基本一致，说明客户抱怨的正是最花钱的问题，可直接按声量沟通。"
    return f"""
<section id="s3">
  <h2><span class="num">03</span>需求与痛点<span class="tag">最吵的问题 ≠ 最贵的问题</span></h2>
  <p class="lead">把工单、NPS 调研、销售拜访、公开舆情与流失原因统一归到 10 个痛点域，
  再按<b>提及广度 30% + 情感强度 20% + 金额暴露 25% + 严重度 15% + 可行动性 10%</b> 加权。
  左列是「说了多少次」，右列是「值多少钱」——两条排名不一致的地方，就是决策容易出错的地方。</p>
  {card("声量排名 vs 影响排名", slope, gap_note)}
  {table(["优先级", "痛点域", "提及范围", "覆盖面", "金额暴露", "评分", "排名变化", "代表性原话", "建议动作"],
         rows, "tbl", ["6%", "11%", "7%", "5%", "8%", "5%", "8%", "25%", "auto"])}
  <p class="foot">共性诉求 {len(common)} 类，P0 {sum(1 for p in pains if p['priority'] == 'P0')} 类。
  原话保留原文以便追溯；完整清单与样本客户见 <code>pain_points.csv</code>。</p>
</section>"""


def sec_opp(s):
    opps = s["opps"]
    k = s["kpi"]
    if not opps:
        return '<section id="s4"><h2><span class="num">04</span>机会点</h2><p class="empty">未识别到机会点。</p></section>'
    type_amount = {}
    for o in opps:
        type_amount[o["opp_type_label"]] = type_amount.get(o["opp_type_label"], 0) + o["est_amount"]
    items = [{"label": k2, "value": v} for k2, v in sorted(type_amount.items(), key=lambda x: -x[1])]

    mix = s.get("evidence_mix") or {}
    colors = {"事实": "#2f7d32", "推断": "#e8a33d", "假设": "#b9c2cd"}
    mix_items = [{"label": f"{lv} {mix.get(lv, 0)} 条", "value": mix.get(lv, 0), "color": colors[lv]}
                 for lv in ["事实", "推断", "假设"] if mix.get(lv)]
    ev_note = (f"共 {k['opp_count']} 条机会：{mix.get('事实', 0)} 条有系统数据直接支撑，"
               f"{mix.get('推断', 0)} 条需销售确认，{mix.get('假设', 0)} 条需先验证场景再投入预算。")

    sigs = s.get("expansion") or []
    sig_html = "".join(
        f'<div class="sig"><div class="sig-h">{esc(e["account_name"][:14])}'
        + "".join(f'<span class="sig-b">{esc(x)}</span>' for x in e["signals"][:2])
        + f'</div><div class="sig-e">{esc(e["evidence"])}</div></div>'
        for e in sigs[:6])
    sig_html = f'<div class="grid-2 sigs">{sig_html}</div>' if sig_html else ""

    # 列数控制在 8 列以内：A4 打印宽度下 11 列会把中文挤成逐字换行
    rows = []
    for o in opps[:16]:
        kind = "defend" if o["amount_type"] == "风险敞口" else "growth"
        rows.append([
            f'{pr_badge(o["priority"])}<div class="sub">评分 {o["score"]:.0f}</div>',
            f'<b>{esc(o["account_name"][:14])}</b>'
            f'<div class="sub">{tier_badge(o["tier"])} {esc(o["industry"])}</div>',
            f'{esc(o["opp_type_label"])}<div class="sub nowrap">{esc(o["product_line"])}</div>',
            f'<span class="num">{money_cell(o["est_amount"], kind)}</span>'
            f'<div class="sub nowrap">{esc(o["amount_type"])}</div>',
            f'<span class="num nowrap">{o["window_days"]}天</span>',
            ev_badge(o.get("evidence_level", "推断")),
            f'<div class="drv">{esc(o["driver"])}</div>',
            f'<div class="act">{esc(o["next_action"])}</div>',
        ])
    return f"""
<section id="s4">
  <h2><span class="num">04</span>机会点与优先级<span class="tag">算得明，还要说清凭什么</span></h2>
  <p class="lead">机会分 = 四维加权（交叉销售 30% + 向上销售 30% + 空白市场 20% + 推荐意愿 20%），
  再按<b>续约风险扣减</b>——风险越高，扩张型机会越打折，先保住盘子再谈增量。
  续约保卫、赢回、首单转化属于「必赢型」，按紧迫度直接排序，不参与折扣。
  每条机会都标注了<b>证据等级</b>：事实可直接跟进，推断需确认，假设需先验证。</p>
  <div class="grid-2">
    {card("机会金额按类型分布", ch.hbar(items, width=470, label_w=112, value_w=96),
          "「续约风险敞口」不是新增收入，与增长型机会分开统计，切勿相加。")}
    {card("证据等级构成", ch.hbar(mix_items, width=470, label_w=112, value_w=80,
                            fmt=lambda v: ""), ev_note)}
  </div>
  <h3>扩张就绪信号<span class="hint">有触发条件的，才值得投售前资源</span></h3>
  {sig_html or '<p class="empty">本期无客户满足扩张触发条件。</p>'}
  <h3>机会清单<span class="hint">按综合评分排序，前 {len(rows)} 条</span></h3>
  {table(["优先级", "客户", "机会 / 品类", "预估金额", "窗口", "证据", "判断依据", "下一步动作"],
         rows, "tbl", ["8%", "14%", "12%", "11%", "6%", "7%", "21%", "auto"])}
  <p class="foot">报告内展示优先级最高的 {len(rows)} 条，全量 {k['opp_count']} 条见
  <code>opportunity_list.csv</code>，可直接导入 CRM 的版本见 <code>crm_opportunities.csv / .xlsx</code>。</p>
</section>"""


def sec_alert(s):
    alerts = s["alerts"]
    if not alerts:
        return '<section id="s5"><h2><span class="num">05</span>趋势预警</h2><p class="ok">本期未触发预警规则。</p></section>'
    rev = s["revenue_series"]
    chart = ch.line([{"name": "整体月消费", "values": rev["values"], "color": "#1f4e79"}],
                    [f"{m[2:]}" for m in rev["labels"]], width=1000, height=220, area=True,
                    y_fmt=lambda v: fmt_money(v), show_legend=False)
    rows = []
    for a in alerts[:14]:
        typ = "流失" if a["alert_type"] == "LOSS" else "增长"
        rows.append([
            lvl_badge(a["level"]),
            f'<span class="sub">{esc(typ)}</span>',
            f'<b>{esc(a["account_name"][:14])}</b><div class="sub">{tier_badge(a["tier"])}</div>',
            esc(a["metric"]),
            f'<div class="drv">{esc(a["evidence"])}</div>',
            f'<div class="act">{esc(a["action"])}</div>',
        ])
    return f"""
<section id="s5">
  <h2><span class="num">05</span>趋势预警<span class="tag">看得早，才有时间动手</span></h2>
  <p class="lead">在月度序列上跑 10 条规则：断崖跌幅、连续下滑、同比恶化、续约高危、活跃断流、负面抬头、
  统计异动（|z|≥2）、配额饱和、加速增长、试用激增。
  <b>P0 需 48 小时内响应，P1 一周内，P2 纳入月度复盘</b>；没有时序证据的项不出 P0。</p>
  {card("组合月消费走势", chart)}
  {table(["级别", "类型", "客户", "指标", "判据与证据", "建议动作"], rows, "tbl",
         ["6%", "5%", "14%", "11%", "32%", "auto"])}
  <p class="foot">P0 {sum(1 for a in alerts if a['level']=='P0')} 条 / P1 {sum(1 for a in alerts if a['level']=='P1')} 条 /
  P2 {sum(1 for a in alerts if a['level']=='P2')} 条。完整清单见 <code>alerts.csv</code>。</p>
</section>"""


def sec_roadmap(s):
    o30, o60, o90 = [], [], []
    for a in s["alerts"]:
        if a["level"] == "P0":
            o30.append(f'{esc(a["account_name"][:12])}：{esc(a["action"])}')
    for o in s["opps"]:
        if o["window_days"] <= 30:
            o30.append(f'{esc(o["account_name"][:12])}：{esc(o["opp_type_label"])} — {esc(o["next_action"])}')
        elif o["window_days"] <= 60:
            o60.append(f'{esc(o["account_name"][:12])}：{esc(o["opp_type_label"])} — {esc(o["next_action"])}')
        else:
            o90.append(f'{esc(o["account_name"][:12])}：{esc(o["opp_type_label"])} — {esc(o["next_action"])}')
    for p in s["pains"]:
        if p["priority"] == "P0":
            o30.append(f'共性痛点治理：{esc(p["category"])} — {esc(p["suggested_action"])}')

    def col(title, sub, items, cls):
        lis = "".join(f"<li>{x}</li>" for x in list(dict.fromkeys(items))[:6]) or "<li class='muted'>—</li>"
        return (f'<div class="rm-col {cls}"><h4>{esc(title)}</h4><div class="rm-sub">{esc(sub)}</div>'
                f"<ul>{lis}</ul></div>")
    return f"""
<section id="s6">
  <h2><span class="num">06</span>30 / 60 / 90 天行动路线<span class="tag">把结论变成排产</span></h2>
  <p class="lead">按「先止血、再拿单、后建设」排产。每条动作都对应到具体客户与负责人
  （负责人见 <code>crm_opportunities.csv</code> 的 Owner 列）。</p>
  <div class="rm">
    {col("0–30 天 · 止血与拿单", "P0 预警处理 + 30 天内窗口机会", o30, "rm-30")}
    {col("30–60 天 · 扩张与验证", "交叉销售 POC、容量锁定", o60, "rm-60")}
    {col("60–90 天 · 培育与建设", "场景空白、用量提升、机制沉淀", o90, "rm-90")}
  </div>
</section>"""


def sec_loop(s, base):
    if not base:
        return f"""
<section id="s7">
  <h2><span class="num">07</span>上期动作闭环<span class="tag">这期没有对比基线</span></h2>
  <div class="loop-empty">本期是<b>首个报告周期</b>，结果已存档为基线快照
  （<code>analysis/history/{esc(s["meta"]["as_of"])}.json</code>）。
  下期用同一份配置再跑一次，这里会自动列出「上次标红的客户这期好转了没有」、
  「上次说好的动作，指标动没动」——这是月度经营会最该看、也最常被漏掉的一页。</div>
</section>"""

    prev_acc = {a["account_id"]: a for a in base.get("accounts", [])}
    cur_acc = {a["account_id"]: a for a in s["accounts"]}
    watch = sorted(set(base.get("p0_accounts") or []) | set(base.get("watch_accounts") or []))
    rows = []
    for aid in watch:
        p, c = prev_acc.get(aid), cur_acc.get(aid)
        if not p:
            continue
        if not c or c["lifecycle"] == "已流失":
            status = '<span class="risk-hi">已流失</span>'
        else:
            d = c["renewal_risk"] - p["risk"]
            if d <= -8:
                status = '<span class="delta good">改善</span>'
            elif d >= 8:
                status = '<span class="delta bad">恶化</span>'
            else:
                status = '<span class="delta flat">持平</span>'
        rows.append([
            f'<b>{esc(p["account_name"][:14])}</b>',
            f'<span class="num">{p["risk"]:.0f}</span>',
            f'<span class="num">{(c or {}).get("renewal_risk", 0):.0f}</span>' if c else "—",
            status,
            f'<span class="num">{fmt_money(p.get("revenue", 0))}</span>',
            f'<span class="num">{fmt_money((c or {}).get("revenue", 0))}</span>' if c else "—",
        ])
    improved = sum(1 for r in rows if "改善" in r[3])
    worsened = sum(1 for r in rows if "恶化" in r[3])
    lost = sum(1 for r in rows if "已流失" in r[3])
    same = len(rows) - improved - worsened - lost
    loop_note = ("恶化客户需在本次经营会上单独过一遍，明确责任人与时间点。"
                 if worsened else "无客户恶化，说明上期防御动作有效，可把资源转向扩张。")

    kpi_rows = []
    keys = [("revenue_window", "窗口消费", "money", False),
            ("growth", "同比增速", "pctpt", False),
            ("alerts_p0", "P0 预警", "int", True),
            ("churn_risk_accounts", "流失预警客户", "int", True),
            ("pain_p0", "P0 痛点", "int", True),
            ("opp_amount_growth", "增长型机会", "money", False)]
    for key, label, kind, invert in keys:
        c = s["kpi"].get(key)
        p = (base.get("kpi") or {}).get(key)
        if kind == "money":
            cs = fmt_money(c or 0)
            ps = fmt_money(p or 0)
            dh = delta_html(c, p, invert=invert, fmt="pct")
        elif kind == "pctpt":
            cs = fmt_pct(c) if c is not None else "—"
            ps = fmt_pct(p) if p is not None else "—"
            if c is None or p is None:
                dh = ""
            else:
                pp = (c - p) * 100
                cls = "flat" if abs(pp) < 0.05 else ("good" if (pp > 0) != invert else "bad")
                dh = "" if abs(pp) < 0.05 else f'<span class="delta {cls}">较上期 {pp:+.1f}pt</span>'
            # 比率指标只能报百分点，报百分比会得出 -54% 这种误导性数字
        else:
            cs, ps = f"{c or 0:.0f}", f"{p or 0:.0f}"
            dh = delta_html(c, p, invert=invert, fmt="int", eps=0)
        kpi_rows.append([label, f'<span class="num">{ps}</span>', f'<span class="num">{cs}</span>', dh])

    prev_p = {x["category"]: x for x in (base.get("pains") or [])}
    cur_p = {x["category"]: x for x in s["pains"]}
    new_p = [c for c in cur_p if c not in prev_p]
    out_p = [c for c in prev_p if c not in cur_p]
    pain_note = ""
    if new_p:
        pain_note += f"新进痛点榜：<b>{esc('、'.join(new_p[:3]))}</b>。"
    if out_p:
        pain_note += f"退出痛点榜：{esc('、'.join(out_p[:3]))}（可能已缓解，也可能是样本变化）。"
    if not pain_note:
        pain_note = "痛点结构稳定，说明问题没有被真正解决，也没有新增。"

    return f"""
<section id="s7">
  <h2><span class="num">07</span>上期动作闭环<span class="tag">对比基线：{esc(base.get("as_of", ""))}</span></h2>
  <p class="lead">上期共 <b>{len(watch)}</b> 家客户被标红：本期改善 <b>{improved}</b> 家、持平 {same} 家、
  恶化 <b>{worsened}</b> 家、已流失 {lost} 家（风险分变动 ≥8 分才算改善/恶化）。
  「上期说要做的事，指标到底动没动」——这一栏比任何新图表都更能说明经营动作是否有效。</p>
  <div class="loop">
    {card("上期标红客户的本期状态", table(["客户", "上期风险", "本期风险", "变化", "上期消费", "本期消费"],
                                     rows, "tbl", ["26%", "12%", "12%", "12%", "auto", "auto"])
          if rows else '<p class="empty">上期无标红客户。</p>', loop_note)}
    {card("整体指标环比", table(["指标", "上期", "本期", "变化"], kpi_rows, "tbl",
                            ["32%", "20%", "20%", "auto"]), pain_note)}
  </div>
</section>"""


def sec_appendix(s):
    mix = s.get("evidence_mix") or {}
    return f"""
<section id="s8" class="appendix">
  <h2><span class="num">08</span>方法与口径说明<span class="tag">可复核，才可信</span></h2>
  <div class="grid-2">
    <div>
      <h4>分层与画像</h4>
      <ul class="bullets">
        <li><b>价值层级</b>：窗口消费额分位（S≥85%、A≥65%、B≥35%、其余 C）；可改用绝对金额阈值。</li>
        <li><b>活跃度</b>：月活/登录/API/资源数/使用率的加权分位，四档（高/中/低/沉睡）。</li>
        <li><b>生命周期</b>：已流失 → 潜在 → 新签(≤6月) → 流失预警（连续下滑/活跃断流/竞品+到期）→ 停滞 → 成长 → 成熟。</li>
        <li><b>一句话根因</b>：取影响最大的 3 个信号拼装，供客户经理复核，不做因果断言。</li>
      </ul>
      <h4>机会金额口径</h4>
      <ul class="bullets">
        <li>交叉销售 = 同行业同类客户该品类年中位消费 × 基准赢率（默认 30%）。</li>
        <li>容量扩容 = 主力品类窗口消费 × 30%（受使用率/资源增速触发）。</li>
        <li>用量提升 = min(同层中位差距 × 50%, 现有消费 × 50%)。</li>
        <li>续约保卫 = 窗口消费 × 流失风险分，记作<b>风险敞口</b>，非增量收入。</li>
        <li>空白场景 = 同行业中位 × 赢率 × 60%。</li>
      </ul>
    </div>
    <div>
      <h4>证据分级</h4>
      <ul class="bullets">
        <li><b>事实</b>（{mix.get('事实', 0)} 条）：直接来自合同、交易、流失记录，可直接进 CRM 跟进。</li>
        <li><b>推断</b>（{mix.get('推断', 0)} 条）：事实基础上的合理外推，如使用率→扩容需求，需销售确认。</li>
        <li><b>假设</b>（{mix.get('假设', 0)} 条）：行业规律推导的可能性，如交叉销售意愿，必须先验证再投预算。</li>
      </ul>
      <h4>使用边界</h4>
      <ul class="bullets">
        <li>金额与概率为<b>相对排序工具</b>，不可直接作为业绩承诺或预算依据。</li>
        <li>客户数少于 10 家时，分位法分层不稳定，建议改用 <code>tier_amount</code> 绝对阈值。</li>
        <li>反馈样本少于 30 条时，痛点广度指标波动大，结论需人工复核。</li>
        <li>「窗口内趋势」增速为估算值，仅在同比数据缺失时使用，报告中已逐条标注。</li>
        <li>涉及个人信息与客户机密数据，报告仅限内部经营分析使用，外发需脱敏。</li>
      </ul>
      <h4>复现方式</h4>
      <pre class="code">python scripts/run_pipeline.py --config config.json</pre>
    </div>
  </div>
</section>"""


# ------------------------------------------------------------------ 组装

def load_template():
    for p in TEMPLATE_CANDIDATES:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    raise SystemExit("找不到报告模板 assets/report_template.html")


def load_baseline(analysis_dir, as_of):
    """取最近一期早于当前基准月的历史快照，用于闭环对比。"""
    hist = os.path.join(analysis_dir, "history")
    if not os.path.isdir(hist):
        return None
    cands = []
    for fn in os.listdir(hist):
        if not fn.endswith(".json"):
            continue
        try:
            d = read_json(os.path.join(hist, fn))
        except Exception:
            continue
        if d.get("as_of") and str(d["as_of"]) < str(as_of):
            cands.append(d)
    cands.sort(key=lambda d: str(d["as_of"]))
    return cands[-1] if cands else None


def kpi_cards(s, base):
    k = s["kpi"]
    bk = (base or {}).get("kpi") or {}

    def d(key, invert=False, kind="pct"):
        if not base or key not in bk or bk.get(key) is None or k.get(key) is None:
            return ""
        return delta_html(k[key], bk[key], invert=invert, fmt=kind)

    g = k.get("growth")
    cards = [
        (f'{k["accounts"]}', f'覆盖客户（NA {k["na_accounts"]}）', d("accounts", kind="int")),
        (f'{fmt_money(k["revenue_window"])}', "窗口消费", d("revenue_window")),
        (f'{fmt_pct(g) if g is not None else "—"}', "同比增速", "",
         "up" if (g or 0) >= 0 else "down"),
        (f'{fmt_money(k.get("opp_amount_growth", 0))}', "增长型机会", d("opp_amount_growth")),
        (f'{fmt_money(k.get("opp_amount_defend", 0))}', "续约风险敞口", d("opp_amount_defend", invert=True), "warn"),
        (f'{k["alerts_p0"]}', "P0 预警", d("alerts_p0", invert=True, kind="int"), "warn"),
        (f'{k["churn_risk_accounts"]}', "流失预警客户", d("churn_risk_accounts", invert=True, kind="int")),
        (f'{k["opp_a"]}', "A 级机会", d("opp_a", kind="int")),
    ]
    out = []
    for c in cards:
        val, label = c[0], c[1]
        extra = c[2] if len(c) > 2 else ""
        cls = c[3] if len(c) > 3 else ""
        delta = f'<span class="kpi-d">{extra}</span>' if extra else ""
        out.append(f'<div class="kpi"><div class="kpi-v {cls}">{val}</div>'
                   f'<div class="kpi-l"><span>{label}</span>{delta}</div></div>')
    return "".join(out)


def build(analysis_dir, out_path=None):
    s = read_json(os.path.join(analysis_dir, "summary.json"))
    base = load_baseline(analysis_dir, s["meta"]["as_of"])
    brand = s.get("brand") or {}
    title = brand.get("report_title") or "客户洞察报告与机会点分析"
    company = brand.get("company") or ""
    portfolio = brand.get("portfolio") or ""

    sections = "".join([
        sec_decisions(s), sec_data(s), sec_seg(s, base), sec_pain(s), sec_opp(s),
        sec_alert(s), sec_roadmap(s), sec_loop(s, base), sec_appendix(s),
    ])

    nav = [(0, "决策"), (1, "数据"), (2, "分层画像"), (3, "痛点"), (4, "机会"),
           (5, "预警"), (6, "路线图"), (7, "上期闭环"), (8, "方法")]

    html = load_template()
    for key, val in {
        "{{TITLE}}": esc(title),
        "{{COMPANY}}": esc(company),
        "{{PORTFOLIO}}": esc(portfolio),
        "{{AS_OF}}": esc(s["meta"]["as_of"]),
        "{{WINDOW}}": f"{s['meta']['window'][0]} ～ {s['meta']['window'][1]}",
        "{{GENERATED_AT}}": esc(s["meta"].get("generated_at") or ""),
        "{{VERDICT}}": verdict(s, base),
        "{{KPI_CARDS}}": kpi_cards(s, base),
        "{{SECTIONS}}": sections,
        "{{NAV}}": "".join(f'<a href="#s{i}">{n}</a>' for i, n in nav),
    }.items():
        html = html.replace(key, val)

    out_path = out_path or os.path.join(analysis_dir, "report.html")
    ensure_dir(os.path.dirname(os.path.abspath(out_path)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Step3 生成 HTML 洞察报告")
    ap.add_argument("--analysis", default="./insight-output/analysis")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    p = build(a.analysis, a.out)
    print(f"✓ 报告已生成 → {os.path.abspath(p)}")


if __name__ == "__main__":
    main()
