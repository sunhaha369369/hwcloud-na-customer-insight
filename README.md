# hwcloud-na-customer-insight

华为云 NA 客户洞察与机会点分析 Skill。把客户数据转成一份可开会、可派活、可导入 CRM 的洞察报告。

> 完整工作流与质量红线见 [SKILL.md](SKILL.md)。本文件只做仓库入口说明。

## 四项能力

| 能力 | 回答的问题 | 主要输出 |
|---|---|---|
| 客户分层与画像 | 谁重要、谁在变差 | S/A/B/C 分层 + 12 种画像标签 + 分层经营策略 |
| 需求与痛点挖掘 | 客户在抱怨什么、先解决哪个 | 痛点双排名（声量 / 加权影响）+ P0/P1/P2 + 原文证据 |
| 机会点识别 | 哪些客户能加钱、加多少 | 交叉/向上/空白/推荐四类机会 + 量化金额 + 证据等级 |
| 趋势预警 | 谁要出事了 | 10 条规则预警，P0 必须带时序证据 |

## 快速开始

需要 Python 3.8+，**零第三方依赖**，不需要 `pip install`。

```bash
# 1. 看成品（自动生成演示数据，12 客户 / 24 个月）
python scripts/run_pipeline.py --demo

# 2. 换自己的数据
cp config.example.json config.json      # 修改数据源与口径
python scripts/ingest.py --config config.json --show-mapping   # 必跑，核对列映射
python scripts/run_pipeline.py --config config.json
```

`--show-mapping` 会打印每个源列映射到了哪个标准字段。接入新数据源时这一步不能跳过——自动映射错了列，后面全错，而且错得很像真的。

## 交付物

- `客户洞察报告.html` — 单文件、内联 SVG 图表，断网可看，可直接打印 PDF
- `客户画像.csv` / 机会清单 CSV — 41 字段，可直接导入 CRM
- `CRM机会清单.xlsx` — 5 个 Sheet
- `data_quality.json` — 数据质量闸门结果

## 目录结构

```
SKILL.md                      技能主文档：工作流 + 质量红线
config.example.json           配置模板（csv/xlsx/json/sqlite/api/db）
assets/report_template.html   A4 打印样式
references/                   7 份方法论文档
scripts/                      8 个脚本 + 4 个自研库（纯标准库）
  lib/xlsx.py                 xlsx 读写
  lib/svgchart.py             内联 SVG 图表
  lib/schema.py               200+ 列名别名映射
```

## 数据要求

7 张标准表，只有 `accounts` 是硬必填。实际起步门槛是 `accounts` + `transactions` 两张——没有消费明细，机会金额和趋势无法成立。

| 表 | 来源系统 | 缺失影响 |
|---|---|---|
| `accounts` | CRM | 无法运行 |
| `transactions` | 计费账单系统 | 机会金额与趋势失效 |
| `behavior` | 控制台运营日志 | 活跃度标缺失，降级为中活跃 |
| `feedback` | 工单 / NPS / 舆情 | 痛点章节无内容 |
| `competitor` | 竞品监控 | 续约风险少一个维度 |
| `churn` | CRM 流失记录 | 流失归因无法并入痛点 |
| `opportunities` | CRM 商机 | 影响很小，仅用于校准赢率 |

字段契约见 [references/data-contract.md](references/data-contract.md)。

## 已知局限

- 不是预测模型，是基于规则与分位的诊断工具
- 无情感字段时，痛点强度靠词典 + 渠道先验估算，不能当问卷结论引用
- `opportunities` 表未参与机会打分
- 华为云内部特有列名（NA 标识、账期口径、产品线分类）需现场核对 `field_overrides`

## 维护约定

- 每次运行自动写入 `analysis/history/<基准月>.json`，保留 24 期，用于生成上期闭环
- `config.json` 已加入 `.gitignore`，真实数据源路径与凭据不入库
- API Token 用环境变量传递（配置里写 `token_env`），不写入配置文件
