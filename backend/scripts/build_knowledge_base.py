"""
瓯医数链 - 知识库构建脚本

功能：
1. 读取 policy_knowledge.json
2. 初始化 KnowledgeBase 并索引所有文档
3. 使用 10 个示例查询测试检索效果
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# 将项目根目录加入 sys.path，确保可以导入 app 包
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.knowledge_base import KnowledgeBase

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 政策知识文件路径
POLICY_JSON_PATH = PROJECT_ROOT.parent / "data" / "policy_knowledge.json"

# ChromaDB 持久化目录
CHROMA_PERSIST_DIR = PROJECT_ROOT / "chroma_data"

# 示例查询列表
SAMPLE_QUERIES = [
    "糖尿病门诊慢病怎么申请？",
    "跨省异地就医报销比例是多少？",
    "DRG付费是什么意思？",
    "大病保险起付线是多少？",
    "医保数据怎么保证安全？",
    "职工医保和居民医保有什么区别？",
    "高血压能办门诊慢病吗？",
    "异地就医怎么备案？",
    "谈判药品双通道是什么？",
    "医保关系转移怎么办理？",
]


async def build_knowledge_base():
    """构建知识库主流程"""
    print("=" * 70)
    print("  瓯医数链 - 知识库构建工具")
    print("=" * 70)

    # ---- 第一步：读取政策文档 ----
    print("\n📖 第一步：读取政策文档...")
    if not POLICY_JSON_PATH.exists():
        print(f"  ❌ 文件不存在: {POLICY_JSON_PATH}")
        return

    with open(POLICY_JSON_PATH, "r", encoding="utf-8") as f:
        documents = json.load(f)
    print(f"  ✅ 成功加载 {len(documents)} 篇政策文档")

    # 打印文档分类统计
    categories = {}
    for doc in documents:
        cat = doc.get("category", "未分类")
        categories[cat] = categories.get(cat, 0) + 1
    print("\n  📊 文档分类统计:")
    for cat, count in categories.items():
        print(f"    - {cat}: {count} 篇")

    # ---- 第二步：初始化知识库 ----
    print("\n🔧 第二步：初始化知识库...")
    kb = KnowledgeBase(
        embedding_api_key="",       # 空 key 将使用 ChromaDB 默认嵌入
        embedding_base_url="https://api.openai.com/v1",
        embedding_model="text-embedding-3-small",
    )
    await kb.initialize(persist_dir=str(CHROMA_PERSIST_DIR))
    print(f"  ✅ 知识库初始化完成，持久化目录: {CHROMA_PERSIST_DIR}")

    # ---- 第三步：索引文档 ----
    print("\n📝 第三步：索引文档...")
    start_time = time.time()
    chunk_count = await kb.index_documents(documents)
    elapsed = time.time() - start_time
    print(f"  ✅ 索引完成: {chunk_count} 个文本块，耗时 {elapsed:.2f} 秒")

    # ---- 第四步：查看知识库统计 ----
    print("\n📊 第四步：知识库统计信息...")
    stats = await kb.get_stats()
    print(f"  - 状态: {stats.get('status', '未知')}")
    print(f"  - 文本块总数: {stats.get('total_chunks', 0)}")
    print(f"  - 分类数量: {stats.get('category_count', 0)}")
    print(f"  - 分类列表: {', '.join(stats.get('categories', []))}")

    # ---- 第五步：测试检索 ----
    print("\n" + "=" * 70)
    print("  🔍 第五步：检索测试（10 个示例查询）")
    print("=" * 70)

    for i, query in enumerate(SAMPLE_QUERIES, 1):
        print(f"\n{'─' * 60}")
        print(f"  查询 {i}: {query}")
        print(f"{'─' * 60}")

        try:
            results = await kb.search(query, top_k=3, min_score=0.0)

            if not results:
                print("  ⚠️ 未找到相关结果")
                continue

            for j, r in enumerate(results, 1):
                print(f"\n  结果 {j} [相关度: {r.score:.4f}]")
                print(f"    📌 标题: {r.title}")
                print(f"    📂 分类: {r.category}")
                print(f"    🏛️ 来源: {r.source}")
                print(f"    📄 内容: {r.content[:150]}{'...' if len(r.content) > 150 else ''}")
                print(f"    🏷️ 类型: {r.metadata.get('chunk_type', '')}")
        except Exception as e:
            print(f"  ❌ 检索失败: {e}")

    # ---- 第六步：测试分类过滤 ----
    print("\n" + "=" * 70)
    print("  🏷️ 第六步：分类过滤测试")
    print("=" * 70)

    test_categories = ["门诊慢病政策", "异地就医政策", "DRG/DIP支付政策"]
    for cat in test_categories:
        results = await kb.search("报销比例", top_k=2, category=cat, min_score=0.0)
        print(f"\n  分类 [{cat}] 检索 '报销比例': 找到 {len(results)} 条结果")
        for r in results:
            print(f"    - {r.title} (score={r.score:.4f})")

    # ---- 第七步：测试单文档查询 ----
    print("\n" + "=" * 70)
    print("  📋 第七步：单文档查询测试")
    print("=" * 70)

    doc = await kb.get_document("policy_001")
    if doc:
        print(f"\n  文档: {doc.get('title', '')}")
        print(f"  分类: {doc.get('category', '')}")
        print(f"  来源: {doc.get('source', '')}")
        print(f"  文本块数: {doc.get('chunk_count', 0)}")

    print("\n" + "=" * 70)
    print("  ✅ 知识库构建与测试完成！")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(build_knowledge_base())
