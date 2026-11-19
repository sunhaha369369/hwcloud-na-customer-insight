---
name: hwcloud-na-customer-insight
description: 面向华为云 NA（重点）客户经营的端到端客户洞察与机会点分析。当需要把客户行为数据、交易/消费记录、反馈舆情、竞品动态等多源数据（CSV / Excel / SQLite / 数据库 / HTTP API）整合起来，自动产出客户分层画像与经营策略、需求痛点优先级、机会点量化排序（交叉销售/向上销售/空白市场/续约风险/推荐意愿）、趋势预警，并交付图文报告与可导入 CRM 的机会清单时使用。触发关键词：客户洞察报告、客户分层、客户画像、客户健康度、NA 客户经营分析、痛点分析、机会点、交叉销售、向上销售、空白市场、续约风险、流失预警、客户机会清单、CRM 商机导入、NPS/工单/舆情分析。
---

# 客户洞察报告与机会点分析

把「一堆客户数据」变成「一份能开会用、能派人干活、能导进 CRM」的交付物。
四件事：**分得清（分层画像）、说得准（痛点）、算得明（机会）、看得早（预警）**。

## 何时使用

- 做季度/月度 NA 客户经营复盘，要从消费、行为、反馈、竞品多张表里出结论；
- 领导要一份「哪些客户要出事、哪些客户能加钱」的判断，而不是一堆原始数据；
- 需要可直接导入 CRM 的机会清单（含金额、优先级、下一步动作、判断依据）；
- 客户经理反馈「不知道先打哪个客户」，需要按机会分与风险排序。

**不适用**：单个客户的定性分析（直接聊更快）、需要实时刷新的 BI 看板（用 BI 工具）、
只有一张客户名单、没有任何交易/行为/反馈数据（先补数据再谈洞察）。

## 工作流

### Step 0 · 先定口径（不要跳）

开工前必须确认四件事，写进 `config.json`：

| 项 | 说明 | 常见错 |
|---|---|---|
| `as_of` | 分析基准月，如 `2026-06` | 用「今天」代替账期最后一个月，导致窗口错位 |
| `window_months` | 分析窗口，默认 12 | 窗口 < 12 却直接算同比 |
| 客户范围 | 全量还是仅 NA | 混入口径后 NA 占比失真 |
| 金额口径 | 含税/不含税、是否含返点 | 与财务口径不一致，报告数字对不上账 |

### Step 1 · 接入数据

按 `references/data-contract.md` 准备 7 张标准表（只有 `accounts` 是硬性必填）。
接入 CSV / Excel / SQLite / API 只需在 `sources` 里加一项，列名靠别名自动映射。

```bash
python scripts/ingest.py --config config.json --show-mapping
```

`--show-mapping` 必跑一次，肉眼确认列映射对不对。没识别到的列用 `field_overrides` 手工指定。

配置里的相对路径**以配置文件所在目录为基准**（不是当前工作目录），把 config 放在项目根目录最省心。
`config.example.json` 里带 db / api / excel 三种源的写法示例，`_` 开头的键不会被执行。

### Step 2 · 跑分析

```bash
python scripts/analyze.py --interim ./insight-output/interim
```

产出 4 份 CSV + `summary.json`。各模型的公式与阈值见：

- 分层/画像/playbook → `references/segmentation.md`
- 痛点分类与优先级 → `references/pain-taxonomy.md`
- 机会四维打分与金额公式 → `references/opportunity-model.md`
- 预警规则 → `references/alert-rules.md`

### Step 3 · 生成报告

```bash
python scripts/render_report.py --analysis ./insight-output/analysis
```

单文件 HTML，图表是内联 SVG（无 CDN 依赖，断网可看，可直接打印 PDF）。
结构遵循**决策优先**：首屏一句话结论 → 00 需要拍板的事 → 证据 → 明细 → 上期闭环。
排版与措辞规范见 `references/report-spec.md`。

### Step 4 · 导出 CRM

```bash
python scripts/export_crm.py --analysis ./insight-output/analysis
```

同时产出中文表头 CSV、英文表头 CSV、多 Sheet XLSX 交付包与校验报告。
字段映射与导入坑见 `references/crm-export.md`。

### Step 3.5 · 上期闭环（自动）

`analyze.py` 每次运行会把结果压缩存到 `analysis/history/<as_of>.json`（最近 24 期）。
`render_report.py` 自动取「早于本期基准月的最近一期」做对比，生成 07 节：

- 上期被标红的客户，本期风险分是改善/持平/恶化/已流失
- 整体 KPI 环比（消费、增速、P0 预警、流失预警客户、P0 痛点、增长型机会）
- 痛点榜的新进与退出

想让闭环有意义，就要**按月用同一份配置定期跑**。只有一期时该节会显示"首期基线"。

### 一键跑完

```bash
python scripts/run_pipeline.py --demo                    # 内置演示数据，30 秒看成品
python scripts/run_pipeline.py --config config.json      # 真实数据
```

## 输出物

| 文件 | 内容 | 给谁看 |
|---|---|---|
| `analysis/report.html` | 首屏结论 + KPI 环比 / 00 决策摘要 / 01 数据与证据构成 / 02 分层画像（价值×风险矩阵）/ 03 痛点（声量 vs 影响）/ 04 机会（含证据等级、扩张信号）/ 05 预警 / 06 30-60-90 路线 / 07 上期闭环 / 08 方法 | 主管、经营会 |
| `analysis/account_profile.csv` | 每客户 41 个字段：分层、生命周期、活跃度、RFM、四维机会分、续约风险、策略与 KPI | 客户经理 |
| `analysis/pain_points.csv` | 痛点域 × 优先级 × 覆盖客户 × 金额暴露 × 原文证据 × 建议动作 | 产品、服务 |
| `analysis/opportunity_list.csv` | 全量机会：类型、金额、口径、评分、判断依据、下一步、时间窗 | 销售 |
| `analysis/alerts.csv` | 预警：级别、指标、时序证据、动作 | 客户经理 |
| `analysis/crm_opportunities.xlsx` | 机会清单 / 客户分层 / 痛点摘要 / 预警清单 / 字段说明 | CRM 管理员 |
| `analysis/history/<基准月>.json` | 本期快照（KPI、客户状态、痛点排名），供下期做闭环对比，自动保留最近 24 期 | 系统自动 |
| `interim/data_quality.json` | 行数、必填缺失、时间覆盖、孤儿记录 | 数据供给方 |

## 质量红线（必须遵守）

1. **金额分两个口径，永不加总**：「增长型预估增量」≠「续约风险敞口」。
   把两者相加成「机会总额」是最常见的错误，会直接导致汇报数字虚高。
2. **金额是排序工具，不是业绩承诺**。对外表述必须带口径（如同业中位 × 赢率 30%）。
3. **客户数 < 10 家时不要用分位分层**，改用 `tier_amount` 绝对阈值，否则 1 家客户就被判成 S。
4. **≤1 家客户提及的痛点不许写成「客户普遍反映」**，只能标「个案」。
5. **估算字段必须标注**：窗口内趋势增速（同比不足时）、按签约周年推断的到期日、情感词典估算的倾向。
6. **没有时序证据的预警不许出 P0**。证据要能被客户经理复核。
7. **数据质量问题写进报告第 01 节**，不要藏起来换一个好看的数字。
8. **每条机会必须标证据等级**（事实/推断/假设）。假设类机会的下一步只能是「先验证」，
   不能写成「直接报价」。证据构成比例要在 01 节公开。
9. **痛点要给两套排名**（按提及量、按加权影响）。只按声量排序会把「不吵但贵」的问题漏掉；
   两者一致时也要明说，不要硬造差距。
10. **每期自动存档快照**，07 节必须回答「上期标红的客户这期好没好」。没有历史时显示
    「首期基线」提示，而不是留白或跳过。

## 交付前自检

```bash
python -c "import json;d=json.load(open('insight-output/analysis/crm_export_report.json'));print(d['importable'], d['errors'], d['warnings'])"
```

逐条核对 `references/report-spec.md` 第 6 节的质检清单，重点是：
摘要数字与 KPI/表格一致、必做清单无同客户重复、痛点引用已脱敏、打印分页正常。

## 常用调参

```json
{
  "tier_amount": {"S": 50000000, "A": 10000000, "B": 2000000},
  "opp_defend_penalty": 0.6,
  "win_rate": 0.25,
  "top_opps": 20,
  "alert_z_threshold": 2.5
}
```

| 想达到的效果 | 改什么 |
|---|---|
| 分层按绝对金额而非分位 | `tier_amount` |
| 更看重保住存量（扩张机会更容易被风险打折） | `opp_defend_penalty` 调到 0.6 |
| 赢率有历史数据校准 | `win_rate` 改成实际值 |
| 机会清单太长销售吃不下 | `top_opps` 调小到 15~20 |
| 预警太吵 | `alert_z_threshold` 调到 2.5 |

## 环境

Python 3.8+，**纯标准库**，无需 `pip install`。xlsx 读写、SQLite、HTTP 全部内置实现。
报告不依赖任何 CDN 或前端框架。

## 参考文件

| 文件 | 什么时候读 |
|---|---|
| `references/data-contract.md` | 接新数据源、字段对不上、要写质量说明 |
| `references/segmentation.md` | 调分层阈值、解释某个客户为什么是这个画像、改 playbook |
| `references/pain-taxonomy.md` | 痛点归类不准、要加新痛点域、解释情感怎么算的 |
| `references/opportunity-model.md` | 质疑机会金额怎么来的、改权重、加机会类型 |
| `references/alert-rules.md` | 预警误报/漏报、调阈值、解释响应级别 |
| `references/report-spec.md` | 改报告结构、改措辞、交付前质检 |
| `references/crm-export.md` | 对接 Salesforce/HubSpot/内部 CRM、导入报错 |
