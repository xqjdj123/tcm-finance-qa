# -*- coding: utf-8 -*-
"""RAG搜索工具：封装rag_module"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool


class RAGTool(BaseTool):
    name = "rag_search"
    description = """
    搜索研报内容。
    用于：分析原因、查竞争格局、找专家观点、解释趋势、找增长驱动因素
    输入：query(搜索词), company(公司名，可选)
    返回：相关研报片段
    不用于：查精确财务数字
    """

    def __init__(self):
        self._rag = None

    def _get_rag(self):
        if self._rag is None:
            from rag_module import RAGRetriever
            self._rag = RAGRetriever()
            self._rag.load()
        return self._rag

    def run(self, inputs: dict) -> dict:
        query = inputs.get("query", "")
        company = inputs.get("company", "")
        top_k = inputs.get("top_k", 3)

        if not query:
            return {"success": False, "error": "缺少query参数"}

        # 拼接搜索词
        search_query = query
        if company and company not in query:
            search_query = company + " " + query

        try:
            rag = self._get_rag()
            results = []

            # 搜chunks
            if rag.chunks_index:
                chunks = rag.search_chunks_hybrid(search_query, top_k)
                for c in chunks:
                    meta = c.get("meta", {})
                    results.append({
                        "type": "chunk",
                        "source": meta.get("source_file", ""),
                        "company": meta.get("company", ""),
                        "section": meta.get("section", ""),
                        "text": meta.get("text", "")[:500],
                        "score": c.get("score", 0),
                    })

            return {
                "success": bool(results),
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
