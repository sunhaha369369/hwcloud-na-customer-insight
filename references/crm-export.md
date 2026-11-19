# CRM 机会清单：字段映射、校验与导入

## 1. 产出文件

| 文件 | 用途 |
|---|---|
| `crm_opportunities.csv` | 中文表头，业务/销售先复核 |
| `crm_opportunities_en.csv` | 英文表头，Salesforce / HubSpot / 内部 CRM 直接映射 |
| `crm_opportunities.xlsx` | 多 Sheet 交付包：机会清单 / 客户分层 / 痛点摘要 / 预警清单 / 字段说明 |
| `crm_export_report.json` | 校验结果（错误、提醒、是否可导入） |

CSV 带 UTF-8 BOM，Excel 双击打开不乱码。

## 2. 字段映射

| 英文字段 | 中文表头 | 说明 |
|---|---|---|
| `opportunity_name` | 机会名称 | `客户｜类型｜品类`，≤120 字 |
| `account_id` | 客户编号 | **必须与 CRM 账户主键对齐**，导入前先核对映射表 |
| `account_name` | 客户名称 | 客户全称 |
| `tier` | 客户分层 | S/A/B/C |
| `industry` | 行业 | 用于行业打法分组 |
| `owner` | 负责人 | 客户经理姓名；导入后需转换为 CRM Owner ID |
| `opportunity_type` | 机会类型 | RENEW_DEFEND / XSELL / UP_CAPACITY / UP_ARPU / WHITESPACE / ACQUIRE / WINBACK |
| `type_label` | 类型名称 | 中文标签 |
| `product_line` | 产品线 | 主推品类 |
| `stage` | 阶段 | 导入初始阶段，默认「需求确认」 |
| `amount` | 预估金额 | 元；口径看 `amount_type` |
| `amount_type` | 金额口径 | 预估年化 / 风险敞口 / 预估首单 / 预估赢回 |
| `currency` | 币种 | CNY |
| `probability` | 赢单概率 | 百分比；A60 / B40 / C20；续约保卫 75、赢回 25、首单 30 |
| `close_date` | 预计成交日期 | 基准月次月 + 建议时间窗 |
| `priority` | 优先级 | A/B/C |
| `score` | 机会分 | 0~100 |
| `next_action` | 下一步动作 | 必须可执行 |
| `next_action_date` | 下一步日期 | 默认 3 个工作日内 |
| `rationale` | 判断依据 | 为什么识别出这条机会 |
| `evidence` | 数据证据 | 支撑数字 |
| `renewal_risk` | 续约风险 | 0~100 |
| `source` | 机会来源 | 固定「客户洞察自动识别」，便于回溯与批量撤回 |

## 3. 导入前校验规则

`export_crm.py` 自动执行，结果写进 `crm_export_report.json`：

| 级别 | 规则 |
|---|---|
| ERROR | 机会名称为空 |
| ERROR | 金额 ≤ 0 |
| ERROR | 客户编号不在客户主数据中 |
| ERROR | 同一客户 + 同类机会 + 同品类重复 |
| WARN | 负责人为空或「未指派」 |
| WARN | 续约风险 ≥60 但仍是扩张型机会（应先做防御） |

`importable = true` 才允许导入。ERROR 必须清零；WARN 可带病导入但要在导入后 1 周内处理。

## 4. 典型 CRM 字段配置

**Salesforce（Opportunity 对象）**

| Salesforce 字段 | 来源列 | 备注 |
|---|---|---|
| Name | 机会名称 | |
| AccountId | 客户编号 | 需先按外部 ID 匹配 Account，不要用名称匹配 |
| StageName | 阶段 | 需与 picklist 值一致，先在沙箱验证 |
| Amount | 预估金额 | |
| CloseDate | 预计成交日期 | |
| Probability | 赢单概率 | |
| OwnerId | 负责人 | 名单 → User ID 映射表 |
| Type / RecordType | 机会类型 | 自定义 picklist |
| Description | 判断依据 + 数据证据 | 拼接后写入 |

**HubSpot（Deal 对象）**：`dealname` / `amount` / `closedate` / `dealstage` / `hubspot_owner_id`，
自定义属性建议加 `insight_score`、`renewal_risk`、`amount_type`。

## 5. 导入流程建议

1. **先小批量试导**：只导 A 级（10~15 条），验证字段映射与阶段流转；
2. **建立外部 ID**：把 `account_id` 写进 CRM 的自定义唯一键，后续可重复导入去重；
3. **回写结果**：把赢单/输单结果回写到 `opportunities` 表，下一期用它校准 `win_rate`；
4. **撤回机制**：`source = 客户洞察自动识别` 的机会可按批次整体撤回，避免污染历史数据。

## 6. 常见坑

| 坑 | 后果 | 规避 |
|---|---|---|
| 用客户名称匹配 Account | 重名客户错挂 | 一律用外部 ID 匹配 |
| 把「风险敞口」当新增收入导入 | 管道金额虚高，预测失真 | `amount_type` 进自定义字段，报表按口径拆分 |
| 阶段直接导成「已提案」 | 销售流程失真 | 统一从「需求确认」进入 |
| 赢单概率写死 50% | 加权管道无意义 | 用机会分映射，或留空由销售填 |
| 一次性导入 30 条给一个人 | 跟进不过来，全部变僵尸 | 按 `owner` 分组，每人每周不超过 5 条新机会 |
