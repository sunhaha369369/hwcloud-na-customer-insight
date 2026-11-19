"""标准数据契约：表定义、字段别名、类型与必填约束。

设计目标：让"任何来源、任何列名"的输入都能映射到统一的 7 张标准表。
新增数据源时只需在 SOURCE_ALIASES 里补别名，无需改分析代码。
"""

# 每张表：字段 -> 类型（str / float / int / month / date / flag）
SCHEMA = {
    "accounts": {
        "required": ["account_id"],
        "fields": {
            "account_id": "str",
            "account_name": "str",
            "industry": "str",
            "region": "str",
            "na_flag": "flag",
            "owner": "str",
            "sign_date": "date",
            "contract_end_date": "date",
            "declared_tier": "str",
            "employee_count": "int",
            "revenue_band": "str",
        },
    },
    "transactions": {
        "required": ["account_id", "amount"],
        "fields": {
            "account_id": "str",
            "month": "month",
            "product_line": "str",
            "product_sku": "str",
            "amount": "float",
            "list_amount": "float",
            "discount_rate": "float",
            "consumption_type": "str",
            "order_id": "str",
        },
    },
    "behavior": {
        "required": ["account_id"],
        "fields": {
            "account_id": "str",
            "month": "month",
            "mau": "int",
            "console_logins": "int",
            "api_calls": "float",
            "resource_count": "int",
            "new_resource_count": "int",
            "ticket_count": "int",
            "usage_ratio": "float",
            "trial_count": "int",
        },
    },
    "feedback": {
        "required": ["text"],
        "fields": {
            "feedback_id": "str",
            "account_id": "str",
            "date": "date",
            "source": "str",
            "channel": "str",
            "sentiment": "str",
            "category": "str",
            "text": "str",
            "severity": "str",
            "escalated": "flag",
            "resolved": "flag",
            "nps": "float",
        },
    },
    "churn": {
        "required": ["account_id"],
        "fields": {
            "account_id": "str",
            "churn_date": "date",
            "reason_code": "str",
            "reason_text": "str",
            "lost_amount": "float",
            "competitor": "str",
        },
    },
    "competitor": {
        "required": ["account_id"],
        "fields": {
            "account_id": "str",
            "date": "date",
            "competitor": "str",
            "signal_type": "str",
            "confidence": "str",
            "est_share_shift": "float",
            "evidence": "str",
        },
    },
    "opportunities": {
        "required": [],
        "fields": {
            "opp_id": "str",
            "account_id": "str",
            "created_date": "date",
            "stage": "str",
            "amount": "float",
            "product_line": "str",
            "outcome": "str",
        },
    },
}

# 别名表：小写化后的列名 -> 标准字段名。中英文混排常见写法全部覆盖。
SOURCE_ALIASES = {
    "account_id": [
        "account", "accountid", "customer_id", "customerid", "cust_id", "client_id",
        "客户id", "客户编号", "客户编码", "客户代码", "客户", "客户号", "用户id", "账号id",
        "公司id", "企业id", "customerno", "customernumber", "crm客户id", "crm_id",
    ],
    "account_name": [
        "accountname", "name", "customer", "customer_name", "client", "company",
        "客户名称", "客户名", "公司名称", "公司", "企业名称", "客户全称", "简称",
    ],
    "industry": ["行业", "所属行业", "行业分类", "一级行业", "行业赛道", "vertical", "sector"],
    "region": ["区域", "地区", "大区", "所在区域", "省份", "地域", "region", "area", "geo", "territory"],
    "na_flag": ["是否na", "na客户", "是否na客户", "是否关键客户", "na", "key_account", "ka_flag", "是否重点"],
    "owner": ["客户经理", "负责人", "销售", "销售负责人", "跟进人", "owner", "ae", "account_owner", "sales"],
    "sign_date": ["签约日期", "开户日期", "合作起始日", "首签日期", "签约时间", "sign_date", "start_date", "onboard_date", "首次签约"],
    "contract_end_date": ["合同到期", "合同到期日", "合同到期时间", "续约日期", "到期日", "服务截止日",
                          "contract_end", "contract_end_date", "renewal_date", "expire_date"],
    "declared_tier": ["客户分级", "分级", "客户等级", "自报分级", "tier", "level", "grade"],
    "employee_count": ["员工数", "员工规模", "人数", "公司规模", "employees", "headcount"],
    "revenue_band": ["营收规模", "年营收", "营收区间", "revenue_band", "annual_revenue"],

    "month": ["月份", "账期", "消费月份", "年月", "账单月份", "统计月份", "month", "period",
              "billing_month", "billingmonth", "stat_month", "日期月份"],
    "product_line": ["产品线", "产品类别", "品类", "服务类型", "产品大类", "云服务品类",
                     "product_line", "productline", "product", "category", "service_type", "lob"],
    "product_sku": ["sku", "产品名称", "产品", "规格", "实例类型", "product_sku", "item", "spec"],
    "amount": ["金额", "消费额", "消费金额", "账单金额", "收入", "费用", "应缴金额", "含税金额",
               "amount", "revenue", "spend", "cost", "billed", "total", "金额(元)"],
    "list_amount": ["目录价", "原价", "标准价", "list_amount", "list_price", "msrp"],
    "discount_rate": ["折扣", "折扣率", "折扣比例", "discount", "discount_rate"],
    "consumption_type": ["计费方式", "消费类型", "付费方式", "付费类型", "consumption_type", "charge_type", "payment_type"],
    "order_id": ["订单号", "订单id", "order_id", "order_no"],

    "mau": ["月活", "mau", "活跃用户数", "活跃用户", "月活跃用户", "活跃账号数", "active_users"],
    "console_logins": ["登录次数", "控制台登录", "登录数", "logins", "login_count", "console_logins"],
    "api_calls": ["api调用", "api调用量", "api调用次数", "接口调用量", "api_calls", "apicalls", "requests"],
    "resource_count": ["资源数", "实例数", "资源数量", "云资源数", "resource_count", "instances", "resources"],
    "new_resource_count": ["新增资源数", "新增实例", "新建资源", "new_resource_count", "new_resources"],
    "ticket_count": ["工单数", "工单量", "工单数量", "ticket_count", "tickets", "case_count"],
    "usage_ratio": ["使用率", "配额使用率", "资源使用率", "用量比例", "usage", "usage_ratio", "utilization"],
    "trial_count": ["试用次数", "试用数", "试用", "trial_count", "trials", "poc_count"],

    "feedback_id": ["反馈id", "工单号", "反馈编号", "feedback_id", "ticket_id", "case_id"],
    "date": ["日期", "时间", "反馈日期", "发生日期", "创建时间", "date", "created_at", "created", "time", "反馈时间"],
    "source": ["来源", "反馈来源", "数据来源", "source", "channel", "渠道", "来源类型"],
    "channel": ["渠道", "渠道类型", "触点", "channel", "touchpoint"],
    "sentiment": ["情感", "情感倾向", "情绪", "情感极性", "满意度", "满意度评分", "csat",
                  "sentiment", "sentiment_score", "emotion", "satisfaction"],
    "category": ["分类", "问题类型", "标签", "问题类别", "主题", "category", "tag", "topic", "type", "问题分类"],
    "text": ["内容", "反馈内容", "工单内容", "描述", "文本", "问题描述", "投诉内容", "意见",
             "text", "content", "description", "comment", "body", "detail", "反馈内容摘要"],
    "severity": ["严重度", "优先级", "紧急程度", "严重程度", "severity", "priority", "urgency"],
    "escalated": ["是否升级", "升级", "是否投诉", "escalated", "is_escalated"],
    "resolved": ["是否解决", "已解决", "解决状态", "resolved", "is_resolved"],
    "nps": ["nps", "推荐意愿", "推荐值", "净推荐值", "推荐分数", "nps_score"],

    "churn_date": ["流失日期", "流失时间", "churn_date", "lost_date", "cancel_date"],
    "reason_code": ["流失原因编码", "原因编码", "原因代码", "reason_code"],
    "reason_text": ["流失原因", "流失原因描述", "离开原因", "reason", "reason_text", "churn_reason"],
    "lost_amount": ["流失金额", "损失金额", "流失消费额", "lost_amount", "lost_revenue", "at_risk_amount"],
    "competitor": ["竞品", "竞争对手", "竞对", "流失竞品", "competitor", "competitor_name", "vendor"],
    "signal_type": ["信号类型", "动向类型", "signal_type", "signal", "event_type"],
    "confidence": ["置信度", "可信度", "confidence", "certainty"],
    "est_share_shift": ["份额变化", "预估份额变化", "份额影响", "est_share_shift", "share_shift"],
    "evidence": ["证据", "依据", "原文", "来源链接", "evidence", "detail", "note", "备注"],
    "opp_id": ["机会id", "商机id", "opp_id", "opportunity_id"],
    "created_date": ["创建日期", "商机创建时间", "created_date", "created"],
    "stage": ["阶段", "商机阶段", "销售阶段", "stage", "sales_stage"],
    "outcome": ["结果", "赢单结果", "结案状态", "outcome", "result", "status"],
}

# 反向索引：别名 -> 标准字段
ALIAS_INDEX = {}
for _canon, _aliases in SOURCE_ALIASES.items():
    ALIAS_INDEX[_canon] = _canon
    for _a in _aliases:
        ALIAS_INDEX[_a.lower().replace(" ", "").replace("_", "").replace("-", "")] = _canon

# 产品线归一化（华为云口径）
PRODUCT_LINE_CANON = {
    "ecs": "计算", "计算": "计算", "弹性云服务器": "计算", "服务器": "计算", "计算服务": "计算",
    "compute": "计算", "cce": "容器", "容器": "容器", "云容器引擎": "容器",
    "obs": "存储", "存储": "存储", "对象存储": "存储", "storage": "存储", "云硬盘": "存储",
    "vpc": "网络", "网络": "网络", "带宽": "网络", "cdn": "网络", "network": "网络", "eip": "网络",
    "rds": "数据库", "数据库": "数据库", "gaussdb": "数据库", "dds": "数据库", "database": "数据库",
    "dcs": "数据库", "redis": "数据库",
    "安全": "安全", "waf": "安全", "hss": "安全", "ddos": "安全", "security": "安全", "主机安全": "安全",
    "ai": "AI", "人工智能": "AI", "modelarts": "AI", "大模型": "AI", "pangu": "AI", "盘古": "AI",
    "大数据": "大数据", "dataarts": "大数据", "dws": "大数据", "mrs": "大数据", "bigdata": "大数据",
    "paas": "PaaS", "中间件": "PaaS", "微服务": "PaaS", "cse": "PaaS", "rom a": "PaaS",
    "saas": "SaaS", "sass": "SaaS", "应用": "SaaS", "行业应用": "SaaS",
    "灾备": "灾备", "备份": "灾备", "dr": "灾备", "存储容灾": "灾备",
    "迁移": "迁移服务", "迁移服务": "迁移服务", "运维": "运维服务", "服务": "服务", "专业服务": "服务",
}

# 生命周期定义（月份为相对 tenure）
LIFECYCLE_ORDER = ["潜在客户", "新签爬坡期", "成长期", "成熟期", "停滞期", "流失预警", "已流失"]

# 价值分层
VALUE_TIERS = ["S", "A", "B", "C"]
VALUE_TIER_LABEL = {"S": "S 战略级", "A": "A 核心级", "B": "B 成长级", "C": "C 长尾级"}

ACTIVITY_LABELS = ["高活跃", "中活跃", "低活跃", "沉睡"]
