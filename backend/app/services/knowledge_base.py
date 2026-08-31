"""
瓯医数链 - 政策知识库服务

基于 ChromaDB 的向量检索知识库，支持：
- 文档分块索引（按 key_points 和 content 分段）
- OpenAI 兼容 API 生成嵌入向量（支持 DeepSeek 等）
- 混合检索（向量相似度 + 关键词匹配）
- 元数据过滤与来源引用
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """检索结果数据类"""
    content: str                    # 匹配的文档片段内容
    source: str                     # 来源机构
    title: str                      # 文档标题
    category: str                   # 政策分类
    score: float                    # 相关度得分（0-1）
    metadata: dict = field(default_factory=dict)  # 额外元数据


class KnowledgeBase:
    """医保政策知识库服务

    使用 ChromaDB 存储文档向量，支持混合检索。
    嵌入向量通过 OpenAI 兼容 API 生成（可对接 DeepSeek 等）。
    """

    # 集合名称
    COLLECTION_NAME = "yibao_policy"

    # 分块参数
    CHUNK_MAX_LENGTH = 800    # 每个分块最大字符数
    CHUNK_OVERLAP = 100       # 分块重叠字符数

    def __init__(
        self,
        embedding_api_key: str = "",
        embedding_base_url: str = "https://api.openai.com/v1",
        embedding_model: str = "text-embedding-3-small",
    ):
        """初始化知识库

        Args:
            embedding_api_key: OpenAI 兼容 API 密钥
            embedding_base_url: API 基础地址（支持 DeepSeek 等）
            embedding_model: 嵌入模型名称
        """
        self._embedding_api_key = embedding_api_key
        self._embedding_base_url = embedding_base_url.rstrip("/")
        self._embedding_model = embedding_model

        self._client: chromadb.ClientAPI | None = None
        self._collection = None
        self._openai_client = None
        self._initialized = False

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    async def initialize(self, persist_dir: str = None) -> None:
        """初始化 ChromaDB 客户端与集合

        Args:
            persist_dir: 持久化目录，为 None 时使用内存模式
        """
        try:
            if persist_dir:
                # 持久化模式：数据保存到磁盘
                os.makedirs(persist_dir, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=persist_dir,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                logger.info("知识库持久化模式，目录: %s", persist_dir)
            else:
                # 内存模式：适合测试
                self._client = chromadb.Client(
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                logger.info("知识库内存模式")

            # 获取或创建集合
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "医保政策知识库"},
            )
            logger.info("集合 '%s' 就绪，现有文档 %d 条", self.COLLECTION_NAME, self._collection.count())

            # 初始化 OpenAI 客户端用于生成嵌入
            self._init_openai_client()

            self._initialized = True
        except Exception as e:
            logger.error("知识库初始化失败: %s", e)
            raise

    def _init_openai_client(self):
        """懒加载 OpenAI 客户端"""
        try:
            from openai import OpenAI
            self._openai_client = OpenAI(
                api_key=self._embedding_api_key or "dummy",
                base_url=self._embedding_base_url,
            )
            logger.info("OpenAI 兼容客户端初始化成功 (base_url=%s)", self._embedding_base_url)
        except ImportError:
            logger.warning("openai 库未安装，将使用 ChromaDB 默认嵌入")
            self._openai_client = None
        except Exception as e:
            logger.warning("OpenAI 客户端初始化失败: %s，将使用默认嵌入", e)
            self._openai_client = None

    # ------------------------------------------------------------------
    # 嵌入向量生成
    # ------------------------------------------------------------------

    def _generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """为文本列表生成嵌入向量

        优先使用 OpenAI 兼容 API，失败时回退到 ChromaDB 默认嵌入函数。
        """
        if not texts:
            return []

        # 尝试使用 OpenAI 兼容 API（仅当显式配置了 key 才走此路径；
        # key 为空时客户端用 dummy key 调用必失败，白等一次连接超时再回退，拖慢每次检索）
        if self._openai_client is not None and self._embedding_api_key:
            try:
                response = self._openai_client.embeddings.create(
                    input=texts,
                    model=self._embedding_model,
                )
                embeddings = [item.embedding for item in response.data]
                logger.debug("通过 API 生成 %d 条嵌入向量", len(embeddings))
                return embeddings
            except Exception as e:
                logger.warning("API 嵌入生成失败: %s，回退到默认嵌入", e)

        # 回退：使用 ChromaDB 默认嵌入（sentence-transformers）
        try:
            from chromadb.utils import embedding_functions
            ef = embedding_functions.DefaultEmbeddingFunction()
            embeddings = ef(texts)
            logger.debug("通过默认嵌入函数生成 %d 条嵌入向量", len(embeddings))
            return embeddings
        except Exception as e:
            logger.error("默认嵌入函数也失败: %s", e)
            raise RuntimeError(f"无法生成嵌入向量: {e}") from e

    # ------------------------------------------------------------------
    # 文档分块
    # ------------------------------------------------------------------

    def _split_document(self, doc: dict) -> list[dict]:
        """将一篇政策文档拆分为多个可检索的文本块

        分块策略：
        1. 每个 key_point 作为一个独立块
        2. content 按段落/小节分割，超长段落按字符数切分
        3. 每个块都携带完整的元数据（标题、分类、来源等）
        """
        chunks: list[dict] = []
        doc_id = doc.get("id", "unknown")
        title = doc.get("title", "")
        category = doc.get("category", "")
        source = doc.get("source", "")
        publish_date = doc.get("publish_date", "")
        effective_date = doc.get("effective_date", "")
        tags = doc.get("tags", [])
        applicable_to = doc.get("applicable_to", "")

        # 基础元数据，每个块都会携带
        base_metadata = {
            "doc_id": doc_id,
            "title": title,
            "category": category,
            "source": source,
            "publish_date": publish_date,
            "effective_date": effective_date,
            "tags": ",".join(tags) if isinstance(tags, list) else str(tags),
            "applicable_to": applicable_to,
        }

        # ---- 块类型 1：摘要块 ----
        summary = doc.get("summary", "").strip()
        if summary:
            chunks.append({
                "id": f"{doc_id}_summary",
                "content": f"【摘要】{title}\n{summary}",
                "metadata": {**base_metadata, "chunk_type": "summary"},
            })

        # ---- 块类型 2：要点块 ----
        key_points = doc.get("key_points", [])
        for i, point in enumerate(key_points):
            point_text = point.strip()
            if point_text:
                chunks.append({
                    "id": f"{doc_id}_kp_{i}",
                    "content": f"【要点】{title}\n{point_text}",
                    "metadata": {**base_metadata, "chunk_type": "key_point", "point_index": i},
                })

        # ---- 块类型 3：正文内容块 ----
        content = doc.get("content", "").strip()
        if content:
            # 先按段落（一、二、三... 或 1. 2. 3. 等编号）分割
            sections = self._split_by_sections(content)
            for i, section in enumerate(sections):
                section = section.strip()
                if not section:
                    continue
                # 如果段落过长，进一步按字符数切分
                if len(section) > self.CHUNK_MAX_LENGTH:
                    sub_chunks = self._split_by_length(section, self.CHUNK_MAX_LENGTH, self.CHUNK_OVERLAP)
                    for j, sub in enumerate(sub_chunks):
                        chunks.append({
                            "id": f"{doc_id}_content_{i}_{j}",
                            "content": f"【正文】{title}\n{sub}",
                            "metadata": {**base_metadata, "chunk_type": "content", "section_index": i},
                        })
                else:
                    chunks.append({
                        "id": f"{doc_id}_content_{i}",
                        "content": f"【正文】{title}\n{section}",
                        "metadata": {**base_metadata, "chunk_type": "content", "section_index": i},
                    })

        logger.debug("文档 '%s' 拆分为 %d 个块", title, len(chunks))
        return chunks

    @staticmethod
    def _split_by_sections(text: str) -> list[str]:
        """按中文编号段落（一、二、三… 或 1. 2. 3.）分割正文"""
        # 匹配 "一、" "二、" 等中文序号 或 "1." "2." 阿拉伯数字序号
        pattern = r"(?=^[一二三四五六七八九十]+、|^\d+\.)"
        sections = re.split(pattern, text, flags=re.MULTILINE)
        # 过滤空段
        return [s for s in sections if s.strip()]

    @staticmethod
    def _split_by_length(text: str, max_length: int, overlap: int) -> list[str]:
        """按固定长度切分文本，保留重叠部分以保持上下文连贯"""
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + max_length
            chunks.append(text[start:end])
            start = end - overlap
        return chunks

    # ------------------------------------------------------------------
    # 文档索引
    # ------------------------------------------------------------------

    async def index_documents(self, documents: list[dict]) -> int:
        """将文档列表索引到知识库

        Args:
            documents: 政策文档列表，每篇包含 id/title/category/content 等字段

        Returns:
            索引的文本块总数
        """
        if not self._initialized:
            raise RuntimeError("知识库未初始化，请先调用 initialize()")

        all_chunks: list[dict] = []
        for doc in documents:
            chunks = self._split_document(doc)
            all_chunks.extend(chunks)

        if not all_chunks:
            logger.warning("没有可索引的文档块")
            return 0

        # 分批处理嵌入生成（每批最多 100 条，避免 API 限流）
        batch_size = 100
        total_indexed = 0

        for batch_start in range(0, len(all_chunks), batch_size):
            batch = all_chunks[batch_start:batch_start + batch_size]
            texts = [c["content"] for c in batch]
            ids = [c["id"] for c in batch]
            metadatas = [c["metadata"] for c in batch]

            try:
                # 生成嵌入向量
                embeddings = self._generate_embeddings(texts)

                # 写入 ChromaDB（upsert 模式，重复 ID 会覆盖）
                self._collection.upsert(
                    ids=ids,
                    documents=texts,
                    embeddings=embeddings,
                    metadatas=metadatas,
                )
                total_indexed += len(batch)
                logger.info("已索引第 %d-%d 块（共 %d 块）", batch_start + 1, batch_start + len(batch), len(all_chunks))
            except Exception as e:
                logger.error("索引批次 %d-%d 失败: %s", batch_start + 1, batch_start + len(batch), e)
                # 尝试逐条索引以跳过问题数据
                for chunk in batch:
                    try:
                        emb = self._generate_embeddings([chunk["content"]])
                        self._collection.upsert(
                            ids=[chunk["id"]],
                            documents=[chunk["content"]],
                            embeddings=emb,
                            metadatas=[chunk["metadata"]],
                        )
                        total_indexed += 1
                    except Exception as inner_e:
                        logger.error("单条索引失败 (%s): %s", chunk["id"], inner_e)

        logger.info("文档索引完成，共索引 %d 个文本块", total_indexed)
        return total_indexed

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        top_k: int = 5,
        category: str = None,
        min_score: float = 0.5,
    ) -> list[SearchResult]:
        """混合检索：向量相似度 + 关键词匹配

        Args:
            query: 用户查询文本
            top_k: 返回最相关的 K 条结果
            category: 按政策分类过滤（如 "门诊慢病政策"）
            min_score: 最低相关度阈值（0-1），低于此值的结果被过滤

        Returns:
            按相关度降序排列的 SearchResult 列表
        """
        if not self._initialized:
            raise RuntimeError("知识库未初始化，请先调用 initialize()")

        # 1. 向量相似度检索。
        # 嵌入生成（首次含 ONNX 模型下载）是重同步 IO，必须放线程池，
        # 否则会阻塞事件循环（曾引发线上 chat 504）。
        vector_results = await asyncio.to_thread(
            self._vector_search, query, top_k * 2, category
        )

        # 2. 关键词检索
        keyword_results = self._keyword_search(query, top_k * 2, category)

        # 3. 合并去重，融合排序
        merged = self._merge_results(vector_results, keyword_results, query)

        # 4. 按 score 降序排列，过滤低分结果
        merged.sort(key=lambda x: x.score, reverse=True)
        final = [r for r in merged if r.score >= min_score]

        return final[:top_k]

    def _vector_search(self, query: str, n_results: int, category: str = None) -> list[SearchResult]:
        """向量相似度检索"""
        try:
            query_embedding = self._generate_embeddings([query])[0]
        except Exception as e:
            logger.error("查询嵌入生成失败: %s", e)
            return []

        where_filter = None
        if category:
            where_filter = {"category": category}

        try:
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error("向量检索失败: %s", e)
            return []

        return self._parse_chroma_results(results, score_type="vector")

    def _keyword_search(self, query: str, n_results: int, category: str = None) -> list[SearchResult]:
        """关键词检索（利用 ChromaDB 的 where_document 过滤）"""
        # 提取查询中的关键词（简单分词：去除停用词，按字/词切分）
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        where_filter = None
        if category:
            where_filter = {"category": category}

        search_results: list[SearchResult] = []

        for kw in keywords:
            try:
                doc_filter = {"$contains": kw}
                combined_where = where_filter
                if where_filter:
                    combined_where = {"$and": [where_filter, {"document": doc_filter}]}

                results = self._collection.query(
                    query_texts=[kw],
                    n_results=n_results,
                    where=combined_where,
                    where_document={"$contains": kw},
                    include=["documents", "metadatas", "distances"],
                )
                search_results.extend(self._parse_chroma_results(results, score_type="keyword"))
            except Exception as e:
                logger.debug("关键词 '%s' 检索失败: %s", kw, e)

        return search_results

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        """从查询中提取关键词

        简单策略：去除常见停用词，保留 2-4 字的词组。
        生产环境建议使用 jieba 等分词工具。
        """
        # 医保领域常见停用词
        stopwords = {"的", "了", "是", "在", "有", "和", "与", "或", "吗", "呢", "啊",
                      "怎么", "什么", "如何", "哪些", "多少", "能不能", "可以", "是否",
                      "我", "你", "他", "她", "它", "这", "那", "个", "一", "不"}

        # 简单按标点和空格切分
        parts = re.split(r"[，。？！、\s]+", query)
        keywords = []
        for part in parts:
            part = part.strip()
            if part and part not in stopwords and len(part) >= 2:
                keywords.append(part)

        # 如果没有提取到关键词，退回原始查询
        return keywords if keywords else [query]

    @staticmethod
    def _parse_chroma_results(results: dict, score_type: str = "vector") -> list[SearchResult]:
        """解析 ChromaDB 查询结果为 SearchResult 列表"""
        search_results: list[SearchResult] = []

        if not results or not results.get("documents"):
            return search_results

        documents = results["documents"][0] if results["documents"] else []
        metadatas = results["metadatas"][0] if results["metadatas"] else []
        distances = results["distances"][0] if results["distances"] else []

        for i, doc in enumerate(documents):
            meta = metadatas[i] if i < len(metadatas) else {}
            dist = distances[i] if i < len(distances) else 1.0

            # 将距离转换为 0-1 的相似度分数
            # ChromaDB 默认使用余弦距离，范围 0-2，0 表示完全相同
            score = max(0.0, 1.0 - dist)

            # 关键词匹配的结果给予适当加分
            if score_type == "keyword":
                score = min(1.0, score + 0.1)

            search_results.append(SearchResult(
                content=doc,
                source=meta.get("source", ""),
                title=meta.get("title", ""),
                category=meta.get("category", ""),
                score=round(score, 4),
                metadata={
                    "doc_id": meta.get("doc_id", ""),
                    "chunk_type": meta.get("chunk_type", ""),
                    "tags": meta.get("tags", ""),
                    "publish_date": meta.get("publish_date", ""),
                    "effective_date": meta.get("effective_date", ""),
                    "applicable_to": meta.get("applicable_to", ""),
                    "score_type": score_type,
                },
            ))

        return search_results

    @staticmethod
    def _merge_results(
        vector_results: list[SearchResult],
        keyword_results: list[SearchResult],
        query: str,
    ) -> list[SearchResult]:
        """融合向量检索和关键词检索的结果

        策略：以 chunk_id (doc_id + chunk_type + content前20字) 去重，
        同时出现在两种检索中的结果获得加权提升。
        """
        merged_map: dict[str, SearchResult] = {}

        def _make_key(r: SearchResult) -> str:
            return f"{r.metadata.get('doc_id', '')}_{r.metadata.get('chunk_type', '')}_{r.content[:20]}"

        # 向量检索结果（权重 0.7）
        for r in vector_results:
            key = _make_key(r)
            r.score = r.score * 0.7
            merged_map[key] = r

        # 关键词检索结果（权重 0.3），重复的叠加分数
        for r in keyword_results:
            key = _make_key(r)
            if key in merged_map:
                # 同时出现在两种检索中，给予额外提升
                merged_map[key].score += r.score * 0.3 + 0.05
                merged_map[key].metadata["score_type"] = "hybrid"
            else:
                r.score = r.score * 0.3
                merged_map[key] = r

        return list(merged_map.values())

    # ------------------------------------------------------------------
    # 单文档查询与分类
    # ------------------------------------------------------------------

    async def get_document(self, doc_id: str) -> dict:
        """根据文档 ID 获取该文档的所有文本块

        Args:
            doc_id: 文档 ID（如 "policy_001"）

        Returns:
            包含文档信息和所有文本块的字典
        """
        if not self._initialized:
            raise RuntimeError("知识库未初始化，请先调用 initialize()")

        try:
            results = self._collection.get(
                where={"doc_id": doc_id},
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.error("获取文档失败 (%s): %s", doc_id, e)
            return {}

        if not results or not results.get("documents"):
            return {}

        # 聚合文本块信息
        metadatas = results["metadatas"] if results["metadatas"] else []
        documents = results["documents"] if results["documents"] else []

        doc_info = {
            "doc_id": doc_id,
            "title": metadatas[0].get("title", "") if metadatas else "",
            "category": metadatas[0].get("category", "") if metadatas else "",
            "source": metadatas[0].get("source", "") if metadatas else "",
            "chunk_count": len(documents),
            "chunks": [],
        }

        for i, doc in enumerate(documents):
            meta = metadatas[i] if i < len(metadatas) else {}
            doc_info["chunks"].append({
                "content": doc,
                "chunk_type": meta.get("chunk_type", ""),
            })

        return doc_info

    async def get_categories(self) -> list[str]:
        """获取知识库中所有政策分类

        Returns:
            去重后的分类列表
        """
        if not self._initialized:
            raise RuntimeError("知识库未初始化，请先调用 initialize()")

        try:
            # 从所有文档的元数据中提取 category 字段
            results = self._collection.get(
                include=["metadatas"],
            )
            metadatas = results.get("metadatas", [])
            categories = list({m.get("category", "") for m in metadatas if m.get("category")})
            return sorted(categories)
        except Exception as e:
            logger.error("获取分类列表失败: %s", e)
            return []

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict:
        """获取知识库统计信息"""
        if not self._initialized:
            return {"status": "未初始化"}

        try:
            count = self._collection.count()
            categories = await self.get_categories()
            return {
                "status": "已初始化",
                "total_chunks": count,
                "categories": categories,
                "category_count": len(categories),
            }
        except Exception as e:
            return {"status": f"错误: {e}"}

    async def load_from_json(self, json_path: str) -> int:
        """从 JSON 文件加载并索引文档

        Args:
            json_path: policy_knowledge.json 文件路径

        Returns:
            索引的文本块总数
        """
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"政策知识文件不存在: {json_path}")

        with open(path, encoding="utf-8") as f:
            documents = json.load(f)

        logger.info("从 %s 加载了 %d 篇政策文档", json_path, len(documents))
        return await self.index_documents(documents)
