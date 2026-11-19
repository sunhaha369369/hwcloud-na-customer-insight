# 数据契约（输入标准表、字段字典与接入方式）

分析代码只认下面 7 张标准表。**任何来源、任何列名**都通过 `ingest.py` 的别名映射归一化到标准表，
所以新增数据源时不需要改分析逻辑，只在 `config.json` 里加一个 source。

## 1. 支持的数据源类型

| type | 必填参数 | 说明 |
|---|---|---|
| `csv` | `path`；可选 `delimiter` | 自动尝试 UTF-8-SIG / UTF-8 / GB18030，自动识别 `\t ; \|` 分隔符 |
| `xlsx` | `path`；可选 `sheet`（名称或序号） | 纯标准库解析，自动识别日期格式单元格 |
| `json` | `path`；可选 `records_path` | `records_path` 支持点路径，如 `data.items` |
| `sqlite` | `path` + `query` 或 `table` | 最省事的数据库接法，建议先把数仓结果落到 sqlite |
| `api` | `url`；可选 `headers`/`params`/`token_env`/`records_path`/`pagination`/`max_pages` | Bearer Token 从环境变量读，**不要把长期凭证写进配置文件** |
| `postgres` / `mysql` | `dsn` 或 host/port/user/password/database + `query` | 需装 `psycopg2-binary` / `pymysql`；生产库建议先导出 CSV |

只给 `input_dir` 时，按文件名关键字自动踩表：`accounts|客户`、`transactions|billing|消费|账单`、
`behavior|usage|行为|活跃`、`feedback|ticket|反馈|工单|舆情`、`churn|流失`、`competitor|竞品`。

## 2. 标准表与字段

### accounts（客户主数据，唯一必填表）
| 字段 | 类型 | 说明 |
|---|---|---|
| `account_id` | str | **必填**，CRM 客户主键，必须与其他表可关联 |
| `account_name` | str | 客户全称 |
| `industry` | str | 行业；机会模型用它做同业对标，缺失会退化为全盘口径 |
| `region` | str | 区域/大区 |
| `na_flag` | flag | 是否 NA 客户（1/0、是/否 均可） |
| `owner` | str | 客户经理；导入 CRM 时需换成 Owner ID |
| `sign_date` | date | 签约/开户日；用于算 tenure 与推断续约周年 |
| `contract_end_date` | date | 合同到期日；缺失时按签约周年估算（报告会标注） |
| `declared_tier` / `employee_count` / `revenue_band` | str/int | 辅助画像信息，不参与打分 |

### transactions（交易/消费，机会金额与趋势的地基）
| 字段 | 类型 | 说明 |
|---|---|---|
| `account_id` | str | 必填 |
| `month` | month | 账期，`2026-06` / `2026/6` / `202606` / `2026-06-15` 都能解析 |
| `product_line` | str | 产品线，自动归一化到 12 个品类（见下） |
| `product_sku` | str | SKU/规格 |
| `amount` | float | **必填**，不含税账单金额（元）；带「万/亿」单位也能解析 |
| `list_amount` / `discount_rate` | float | 用于折扣分析 |
| `consumption_type` | str | 按需/包年包月/预留实例 |

品类归一化表：计算、存储、网络、数据库、容器、安全、大数据、AI、PaaS、SaaS、灾备、服务。
别名（ECS/OBS/RDS/ModelArts 等）在 `lib/schema.py: PRODUCT_LINE_CANON` 里维护。

### behavior（行为活跃）
`account_id`、`month`、`mau`、`console_logins`、`api_calls`、`resource_count`、
`new_resource_count`、`ticket_count`、`usage_ratio`（0~1 或 0~100 都行）、`trial_count`。

### feedback（反馈/工单/舆情）
`account_id`、`feedback_id`、`date`、`source`、`channel`、`sentiment`、`category`、
`text`、`severity`、`escalated`、`resolved`、`nps`。

- `sentiment` 支持 `正/中/负`、`-1~1`、`1~5`、`0~10` 四种刻度，自动归一化。
- 完全没有情感字段时，用「文本情感词典 × 渠道先验」估算：工单 −0.35、投诉 −0.60、流失原因 −0.85、
  NPS 调研 −0.15、公开舆情 −0.35。**这一列是估算值，报告里不要当作问卷结论引用。**

### churn（流失记录）
`account_id`、`churn_date`、`reason_code`、`reason_text`、`lost_amount`、`competitor`。
流失原因会自动并入痛点分析（情感固定为 −0.8、严重度 1.0）。

### competitor（竞品动态）
`account_id`、`date`、`competitor`、`signal_type`、`confidence`（高/中/低）、`est_share_shift`、`evidence`。
`confidence` 转成 0.9/0.6/0.3，取客户下的最大值作为「竞品信号强度」，进入续约风险模型。

### opportunities（历史商机，可选）
用于后续做赢率校准与去重，当前版本只做留档读取，不参与打分。

## 3. 列名别名

`lib/schema.py: SOURCE_ALIASES` 收录了 200+ 常见写法（中英文、含「客户编号/客户ID/account_id」等）。
匹配规则：**精确匹配 → 别名索引 → 子串兜底**，且同一标准字段只被占用一次（避免误配）。

仍配不上时用 `field_overrides` 手工指定：

```json
"field_overrides": { "transactions": { "本月实际收入(元)": "amount" } }
```

用 `python scripts/ingest.py --config config.json --show-mapping` 打印实际映射，接入新源必跑一次。

## 4. 质量闸门

`interim/data_quality.json` 会记录每张表的行数、必填缺失率、全空字段、时间覆盖与孤儿记录。
出现下列情况时，报告必须相应降级表述：

| 情况 | 处理 |
|---|---|
| 窗口内交易月份 < 12 | 增速改用「窗口内趋势」估算并标注 |
| 交易数据 < 6 个月 | 趋势/同比结论标注置信度低 |
| 缺少 behavior | 活跃度维度标「数据缺失」，机会模型按中活跃处理 |
| 缺少 feedback | 痛点章节标注「无反馈数据」，不出共性结论 |
| 缺少 competitor | 续约风险只由合同/趋势/舆情三项构成 |
| feedback 全部无法关联客户 | 痛点只做全局层面，禁止下钻到客户 |
| 孤儿交易（客户主数据缺失） | 自动补最小档案并在质量报告中列出 |
