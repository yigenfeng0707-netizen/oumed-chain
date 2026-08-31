"""
瓯医数链 - 数据管家引擎（Data Lake Engine，湖仓一体）

数据管家智能体（Data Agent）的核心模块，提供：
- 湖仓一体数据资产目录（仓层结构化主题表 + 湖层原始数据文件）
- 智能数据查询（NL2SQL：LLM 优先生成只读 SQL，降级到预置查询模板）
- SQL 安全校验（只允许 SELECT、表白名单、强制 LIMIT）
- 数据质量与血缘检查

技术栈：SQLAlchemy text() 只读执行 + 规则模板引擎，零外部依赖，可离线运行。
设计原则：只读、可解释（每次查询返回所用 SQL 与口径）、演示不翻车（无 LLM 时模板兜底）。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ============================================================
# 常量：查询行数上限 / 数据目录路径
# ============================================================

MAX_ROWS = 1000  # 所有查询强制行数上限

# engine.py 位于 backend/app/services/data_lake/，数据文件在项目根目录 data/
# 向上 5 层：data_lake/ → services/ → app/ → backend/ → 项目根
_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    "data",
)

# ============================================================
# 仓层：结构化主题表元数据（业务含义 + 血缘）
# ============================================================

WAREHOUSE_TABLES: dict[str, dict[str, str]] = {
    "users": {"label": "参保人基本信息", "lineage": "data/users.csv → init_db 加载"},
    "insurance_records": {"label": "逐月医保缴费记录", "lineage": "data/insurance_records.csv → init_db 加载"},
    "medical_records": {"label": "就医记录（诊断/费用/报销）", "lineage": "data/medical_records.csv → init_db 加载"},
    "medication_records": {"label": "购药/用药记录", "lineage": "data/medication_records.csv → init_db 加载"},
    "policy_documents": {"label": "医保政策知识库", "lineage": "data/policy_knowledge.json → build_knowledge_base 构建"},
    "data_authorizations": {"label": "数据授权记录", "lineage": "用户在前端授权操作实时写入"},
    "eeg_records": {"label": "脑电评估记录", "lineage": "EEG 引擎采集会话实时写入"},
    "imaging_records": {"label": "影像检查记录", "lineage": "影像引擎分析会话实时写入"},
    "body_documents": {"label": "档案资料存档（上传原文）", "lineage": "档案管家上传接口实时写入（湖层原文入仓）"},
    "body_records": {"label": "人体健康档案记录", "lineage": "档案管家从对话/资料抽取归档（只增不删）"},
}

# 湖层：原始/半结构化数据资产（目录扫描得到，此处登记业务含义）
LAKE_ASSET_LABELS: dict[str, str] = {
    "users.csv": "参保人种子数据",
    "insurance_records.csv": "缴费记录种子数据",
    "medical_records.csv": "就医记录种子数据",
    "medication_records.csv": "用药记录种子数据",
    "mock_data.json": "演示兜底数据集",
    "policy_knowledge.json": "政策知识原文",
    "reimbursement_rules.json": "报销规则库",
    "drug_interaction_rules.json": "药物相互作用规则库",
    "eeg_policy_link.json": "脑电-医保政策联动规则库",
}

# ============================================================
# 预置查询模板（无 LLM 时的降级方案，SQLite/PostgreSQL 双兼容）
# ============================================================

QUERY_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "cost_summary",
        "label": "就医费用汇总",
        "keywords": ["费用", "花费", "总费用", "汇总", "花了多少", "医疗支出"],
        "time_range": "全部历史",
        "datasets": ["medical_records"],
        "sql": ("SELECT visit_type, COUNT(*) AS visit_count, "
                "SUM(total_cost) AS total_cost, SUM(reimbursed_amount) AS reimbursed "
                "FROM medical_records GROUP BY visit_type ORDER BY total_cost DESC"),
    },
    {
        "id": "hospital_rank",
        "label": "就医医院排行",
        "keywords": ["医院", "哪家医院", "就诊最多"],
        "time_range": "全部历史",
        "datasets": ["medical_records"],
        "sql": ("SELECT hospital, COUNT(*) AS visit_count, SUM(total_cost) AS total_cost "
                "FROM medical_records GROUP BY hospital ORDER BY visit_count DESC LIMIT 10"),
    },
    {
        "id": "medication_top",
        "label": "用药 TOP10",
        "keywords": ["用药", "药品", "药物", "哪些药", "买药", "购药"],
        "time_range": "全部历史",
        "datasets": ["medication_records"],
        "sql": ("SELECT medication_name, category, SUM(quantity) AS total_quantity, "
                "SUM(quantity * unit_price) AS total_amount "
                "FROM medication_records GROUP BY medication_name, category "
                "ORDER BY total_quantity DESC LIMIT 10"),
    },
    {
        "id": "insurance_payments",
        "label": "分年度缴费统计",
        "keywords": ["缴费", "参保缴费", "交了多少钱", "缴费基数"],
        "time_range": "按年度",
        "datasets": ["insurance_records"],
        "sql": ("SELECT year, COUNT(*) AS months, SUM(personal_amount) AS personal_total, "
                "SUM(company_amount) AS company_total "
                "FROM insurance_records GROUP BY year ORDER BY year"),
    },
    {
        "id": "eeg_trend",
        "label": "脑电评估记录",
        "keywords": ["脑电", "压力", "注意力", "睡眠", "认知负荷", "情绪"],
        "time_range": "最近 20 条",
        "datasets": ["eeg_records"],
        "sql": ("SELECT recorded_at, mental_state_label, alert_count, policy_link_count "
                "FROM eeg_records ORDER BY recorded_at DESC LIMIT 20"),
    },
    {
        "id": "imaging_stats",
        "label": "影像检查统计",
        "keywords": ["影像", "CT", "胸片", "核磁", "MRI", "X光"],
        "time_range": "全部历史",
        "datasets": ["imaging_records"],
        "sql": ("SELECT study_type, risk_level, COUNT(*) AS study_count "
                "FROM imaging_records GROUP BY study_type, risk_level ORDER BY study_count DESC"),
    },
    {
        "id": "users_overview",
        "label": "参保人构成",
        "keywords": ["多少人", "用户数", "参保人", "参保类型", "有几个人"],
        "time_range": "当前",
        "datasets": ["users"],
        "sql": ("SELECT insurance_type, employee_status, COUNT(*) AS user_count "
                "FROM users GROUP BY insurance_type, employee_status ORDER BY user_count DESC"),
    },
    {
        "id": "body_archive_overview",
        "label": "健康档案记录统计",
        "keywords": ["档案", "归档", "档案记录"],
        "time_range": "全部历史",
        "datasets": ["body_records"],
        "sql": ("SELECT organ, source_label, COUNT(*) AS record_count "
                "FROM body_records GROUP BY organ, source_label ORDER BY record_count DESC"),
    },
]

# ============================================================
# SQL 安全校验
# ============================================================

# 禁止出现的写操作/危险语句（单词边界匹配）
_FORBIDDEN_TOKENS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "REPLACE", "ATTACH", "DETACH", "PRAGMA", "GRANT", "REVOKE", "VACUUM", "REINDEX",
]


def validate_sql(sql: str) -> tuple[bool, str, str]:
    """校验并规范化只读 SQL。

    Returns:
        (ok, normalized_sql, reason) — ok=False 时 reason 说明拒绝原因。
    """
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        return False, "", "SQL 为空"

    # 禁止多语句
    if ";" in cleaned:
        return False, "", "不允许多条语句（仅支持单条只读查询）"

    upper = cleaned.upper()

    # 必须以 SELECT / WITH 开头
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False, "", "仅允许只读查询（SELECT），拒绝执行写操作"

    # 禁止危险关键字
    for token in _FORBIDDEN_TOKENS:
        if re.search(rf"\b{token}\b", upper):
            return False, "", f"检测到禁止语句: {token}"

    # 表白名单校验（FROM / JOIN 后的表名必须在已知表内）
    tables = set(re.findall(r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", upper))
    unknown = tables - {t.upper() for t in WAREHOUSE_TABLES}
    if unknown:
        return False, "", f"涉及未知数据表: {', '.join(sorted(unknown))}"

    # 强制行数上限
    limit_match = re.search(r"\bLIMIT\s+(\d+)", upper)
    if limit_match is None:
        cleaned = f"{cleaned} LIMIT {MAX_ROWS}"
    elif int(limit_match.group(1)) > MAX_ROWS:
        cleaned = re.sub(r"(?i)\bLIMIT\s+\d+", f"LIMIT {MAX_ROWS}", cleaned)

    return True, cleaned, ""


def build_nl2sql_prompt(question: str) -> str:
    """构造 NL2SQL 提示词：给定表结构说明，要求只返回一条只读 SQL。"""
    schema_lines = [f"- {name}: {meta['label']}" for name, meta in WAREHOUSE_TABLES.items()]
    return (
        "你是医疗数据仓库的 SQL 专家。请把用户的自然语言问题翻译成一条只读 SQL 查询。\n"
        "可用数据表（SQLite/PostgreSQL 兼容）：\n"
        + "\n".join(schema_lines)
        + "\n\n要求："
        "1) 只输出一条 SELECT 语句，不要任何解释、注释或 markdown 代码块标记；"
        "2) 只能使用上面列出的表；"
        "3) 不要使用数据库方言特有函数，聚合优先用 COUNT/SUM/AVG；"
        "4) 涉及个人数据时按 user_id 过滤（如用户未指定则不过滤）；"
        "5) 无法翻译时只输出: UNSUPPORTED\n\n"
        f"用户问题：{question}"
    )


def extract_sql_from_llm(response: str) -> str | None:
    """从 LLM 回答中提取 SQL（兼容 ```sql 代码块包裹）。"""
    if not response:
        return None
    if "UNSUPPORTED" in response.upper():
        return None
    fenced = re.search(r"```(?:sql)?\s*(SELECT.+?)```", response, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    bare = re.search(r"(SELECT\s+.+)", response, re.IGNORECASE | re.DOTALL)
    return bare.group(1).strip() if bare else None


# ============================================================
# 模板匹配（降级方案）
# ============================================================

def match_template(message: str) -> tuple[dict[str, Any] | None, int]:
    """按关键词命中数为自然语言问题匹配预置查询模板。"""
    best, best_score = None, 0
    for tpl in QUERY_TEMPLATES:
        score = sum(1 for kw in tpl["keywords"] if kw in message)
        if score > best_score:
            best, best_score = tpl, score
    return best, best_score


# ============================================================
# 湖层资产扫描
# ============================================================

def scan_lake_assets(data_dir: str = "") -> list[dict[str, Any]]:
    """扫描 data/ 目录，登记湖层原始数据资产（文件大小 + 业务含义）。"""
    root = data_dir or _DATA_DIR
    assets: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        logger.warning("湖层数据目录不存在: %s", root)
        return assets
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isfile(path) and not name.startswith("."):
            assets.append({
                "name": name,
                "label": LAKE_ASSET_LABELS.get(name, "原始数据文件"),
                "size_bytes": os.path.getsize(path),
                "format": name.rsplit(".", 1)[-1].lower() if "." in name else "unknown",
            })
        elif os.path.isdir(path):
            # receipts/ 等子目录按目录资产登记
            file_count = sum(len(files) for _, _, files in os.walk(path))
            assets.append({
                "name": name + "/",
                "label": "票据影像原件（OCR 演示素材）" if name == "receipts" else "原始数据目录",
                "size_bytes": None,
                "format": "directory",
                "file_count": file_count,
            })
    return assets


# ============================================================
# 执行与查询入口
# ============================================================

def _json_safe(value: Any) -> Any:
    """将查询结果值转为 JSON 可序列化类型。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


async def execute_query(session, sql: str) -> dict[str, Any]:
    """执行已校验的只读 SQL，返回结构化结果（列名 + 行数据）。"""
    result = await session.execute(text(sql))
    columns = list(result.keys()) if result.keys() else []
    rows = [[_json_safe(v) for v in row] for row in result.fetchall()]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


async def _count_rows(session, table: str) -> int | None:
    """统计表行数（表不存在/查询失败返回 None）。"""
    try:
        result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
        return int(result.scalar() or 0)
    except Exception:
        return None


async def catalog(session) -> dict[str, Any]:
    """湖仓一体数据资产目录：仓层表（含实时行数）+ 湖层文件资产 + 血缘说明。"""
    warehouse = []
    total_rows = 0
    for name, meta in WAREHOUSE_TABLES.items():
        count = await _count_rows(session, name)
        if count is not None:
            total_rows += count
        warehouse.append({
            "table": name,
            "label": meta["label"],
            "layer": "warehouse",
            "row_count": count,
            "lineage": meta["lineage"],
        })
    lake = scan_lake_assets()
    return {
        "warehouse": warehouse,
        "lake": lake,
        "summary": {
            "warehouse_tables": len(warehouse),
            "warehouse_total_rows": total_rows,
            "lake_assets": len(lake),
        },
    }


async def smart_query(session, message: str, llm=None, user_id: str | None = None) -> dict[str, Any]:
    """智能数据查询：模板匹配（确定性）→ LLM NL2SQL → 目录兜底。

    返回结构对齐 DATA_AGENT_PROMPT 的输出格式（answer_summary / query /
    result_table / data_source / quality_notes）。
    """
    message = (message or "").strip()

    # 1. 预置模板优先（确定性、可离线）
    template, score = match_template(message)

    sql, source, tpl = None, "", None
    if template is not None and score > 0:
        sql, source, tpl = template["sql"], "template", template
    elif llm is not None:
        # 2. LLM 生成 SQL（经安全校验后才执行）
        try:
            response = await llm.chat(
                messages=[{"role": "user", "content": build_nl2sql_prompt(message)}],
                temperature=0.0,
            )
            candidate = extract_sql_from_llm(response or "")
            if candidate:
                ok, safe_sql, reason = validate_sql(candidate)
                if ok:
                    sql, source = safe_sql, "llm"
                else:
                    logger.warning("LLM 生成 SQL 被安全校验拒绝: %s | SQL: %s", reason, candidate[:200])
        except Exception as e:
            logger.warning("LLM NL2SQL 失败: %s，降级模板", e)

    # 3. 仍无 SQL：返回目录摘要兜底
    if sql is None:
        cat = await catalog(session)
        s = cat["summary"]
        return {
            "answer_summary": (
                f"暂未将该问题翻译为查询。湖仓当前共有 {s['warehouse_tables']} 张仓层表"
                f"（{s['warehouse_total_rows']} 条记录）、{s['lake_assets']} 项湖层资产，"
                "可换个说法试试，例如\"帮我汇总就医费用\"或\"用药 TOP10 是哪些\"。"
            ),
            "query": {"natural_language": message, "sql": None, "row_count": 0,
                      "time_range": "", "filters": [], "source": "fallback"},
            "result_table": {"columns": [], "rows": []},
            "data_source": {"layer": "warehouse", "datasets": []},
            "quality_notes": [],
            "catalog": cat,
        }

    try:
        table = await execute_query(session, sql)
    except Exception as e:
        logger.error("数据查询执行失败: %s | SQL: %s", e, sql)
        return {
            "answer_summary": f"查询执行失败（{e}），请换一种问法。",
            "query": {"natural_language": message, "sql": sql, "row_count": 0,
                      "time_range": "", "filters": [], "source": source},
            "result_table": {"columns": [], "rows": []},
            "data_source": {"layer": "warehouse", "datasets": []},
            "quality_notes": [],
        }

    label = tpl["label"] if tpl else "自定义查询"
    time_range = tpl.get("time_range", "") if tpl else ""
    datasets = tpl.get("datasets", []) if tpl else _extract_tables(sql)
    if table["row_count"] == 0:
        summary = f"查询完成（{label}），未查询到相关记录。"
    else:
        summary = f"查询完成（{label}），共 {table['row_count']} 条记录，统计口径：{time_range or '当前数据'}。"

    return {
        "answer_summary": summary,
        "query": {
            "natural_language": message,
            "sql": sql,
            "row_count": table["row_count"],
            "time_range": time_range,
            "filters": [f"user_id={user_id}"] if user_id else [],
            "source": source,
        },
        "result_table": table,
        "data_source": {"layer": "warehouse", "datasets": datasets},
        "quality_notes": [],
    }


def _extract_tables(sql: str) -> list[str]:
    """从 SQL 中提取表名（小写）。"""
    return [t.lower() for t in re.findall(r"(?i)(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", sql)]


# ============================================================
# 数据质量检查
# ============================================================

QUALITY_CHECKS: list[dict[str, str]] = [
    {"table": "medical_records", "check": "报销额>总费用（口径异常）",
     "sql": "SELECT COUNT(*) FROM medical_records WHERE reimbursed_amount > total_cost"},
    {"table": "medical_records", "check": "诊断为空",
     "sql": "SELECT COUNT(*) FROM medical_records WHERE diagnosis IS NULL OR diagnosis = ''"},
    {"table": "medication_records", "check": "单价非正数",
     "sql": "SELECT COUNT(*) FROM medication_records WHERE unit_price <= 0"},
    {"table": "insurance_records", "check": "缴费金额非正数",
     "sql": "SELECT COUNT(*) FROM insurance_records WHERE base_amount <= 0"},
]


async def quality_report(session) -> dict[str, Any]:
    """数据质量报告：逐条执行预置检查 + 血缘概览。"""
    results = []
    issues = 0
    for check in QUALITY_CHECKS:
        count = await _count_rows(session, check["table"])
        if count is None:  # 表不存在，跳过
            continue
        try:
            result = await session.execute(text(check["sql"]))
            hit = int(result.scalar() or 0)
        except Exception:
            hit = None
        status = "unknown" if hit is None else ("issue" if hit > 0 else "pass")
        if hit and hit > 0:
            issues += 1
        results.append({
            "table": check["table"],
            "check": check["check"],
            "hits": hit,
            "status": status,
        })
    return {
        "checks": results,
        "issue_count": issues,
        "lineage_note": "数据血缘：data/*.csv 种子文件 → init_db 载入仓层；对话/上传/采集数据实时写入对应主题表。",
    }


# ============================================================
# 对话回答组织（供编排器使用）
# ============================================================

def format_chat_response(result: dict[str, Any], max_rows: int = 5) -> str:
    """把查询结果组织成 Markdown 对话回答：结论在前，明细表 + SQL 透明展示。"""
    parts = [f"**📊 {result['answer_summary']}**"]
    table = result.get("result_table") or {}
    columns, rows = table.get("columns") or [], table.get("rows") or []
    if columns and rows:
        parts.append("\n| " + " | ".join(str(c) for c in columns) + " |")
        parts.append("|" + "|".join(["---"] * len(columns)) + "|")
        for row in rows[:max_rows]:
            parts.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
        if len(rows) > max_rows:
            parts.append(f"\n（共 {len(rows)} 行，仅展示前 {max_rows} 行）")
    sql = (result.get("query") or {}).get("sql")
    if sql:
        parts.append(f"\n```sql\n{sql}\n```")
        parts.append(f"口径说明：{(result.get('query') or {}).get('time_range') or '当前全量数据'}；"
                     f"查询来源：{'预置模板' if (result.get('query') or {}).get('source') == 'template' else '智能生成'}。")
    for note in result.get("quality_notes") or []:
        parts.append(f"⚠️ 数据质量提示：{note}")
    return "\n".join(parts)
