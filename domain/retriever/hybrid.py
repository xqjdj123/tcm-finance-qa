# -*- coding: utf-8 -*-
"""升级版混合检索器 — 包装 rag_module.py 的 FAISS+BM25+RRF

本模块对外提供三件事：
  1. FAISS + BM25 双路 + RRF 融合检索（而非 agent 原来只用的 BM25）
  2. company 元数据后处理（加权 / 严格过滤，见 company_mode）
  3. 查询归一化 normalize_query（纯字符串、零成本，默认）
     与可选的 LLM 改写 llm_rewrite_query（开关关闭时不用）

⚠️ 命名如实（2026-09 修正）：
  历史上本模块注释写“使用 LLM 改写查询（而非简单字符串拼接）”，
  但实现只是“删语气词 + 拼公司名”的字符串处理 —— 名实不符，
  在论文里写成“已实现 LLM 改写”属送命题。现已拆成两个方法：
    · normalize_query    —— 默认路径，**不调用 LLM**，行为与旧实现一致
    · llm_rewrite_query  —— 真·LLM 改写，需显式开启 rag.llm_rewrite
  对外统一入口 rewrite_query() 按开关分发，任何 LLM 异常都回退归一化。
"""
import sys
import os
from typing import List, Dict, Optional


def _retriever_cfg() -> dict:
    """检索层参数（config.yaml `retriever.*`）。

    缺配置时回退到"接线前的硬编码值"，故接线本身不改变行为（与 rag_module.py 同款）。

    ⚠️ 缺陷修复（2026-09-16，记为 **N9**）：
      本函数由 `275f75c`「no-hardcode 唯一事实源收敛」引入调用，但**当时没有一并定义**
      ——`__init__` 里 `cfg = _retriever_cfg()` 直接抛 `NameError`，导致
      `HybridRetriever` 无法实例化、`rag_search` 工具经 `ToolRegistry.execute`
      的异常兜底**全程返回 `{"error": "name '_retriever_cfg' is not defined"}`**。
      黑盒证据（收敛率 1.000）照不出"工具是死的"——属 N 类正确性缺陷。
      影响面见 `doc/修改总结/2026-09-16.md §3`。
    """
    try:
        from agent.config import get_retriever_config
        return get_retriever_config()
    except Exception:
        return {"rrf_k": 60, "fusion_top_k": 5,
                "company_boost": 2.0, "candidate_multiplier": 4,
                "channel_candidate_multiplier": 3}


class HybridRetriever:
    """
    混合检索器 — 包装 rag_module.RAGRetriever

    改进:
      1. 启用 FAISS + BM25 双路 + RRF 融合（而非 agent 原来只用的 BM25）
      2. company 元数据后处理（加权/过滤），替代“只把公司名拼进查询串”
      3. 查询归一化（默认）或可选 LLM 改写（开关）
    """

    # ---- company 元数据处理参数 ----
    # 背景（2026-09 实测 models/rag_index/index_meta.json，共 4359 条）：
    #   company='行业综述' 占 3953 条 = 90.7%，真实公司合计仅 406 条
    #   （片仔癀 60 / 天士力 42 / 东阿阿胶 35 / 云南白药 33 / 华润三九 31 /
    #     佐力药业 31 / 昆药集团 28 / … / 白云山 16 / 马应龙 3）。
    #   因此 **硬过滤会一刀切掉 90% 语料（含行业与同业上下文）**，未必提升效果。
    #   默认走加权（保召回），严格模式需显式指定，并须用 A/B 数据说话。
    # 类级兜底值（与 config.yaml 默认一致）；__init__ 会用配置覆盖。
    COMPANY_BOOST = 2.0          # 命中 company 的 chunk 分数倍数
    CANDIDATE_MULTIPLIER = 4     # 有 company 时先取更宽的候选池，供加权/过滤后截断

    def __init__(self):
        # 2026-09 接线：两个参数此前内联写死，config.yaml 同名键从未生效。
        cfg = _retriever_cfg()
        self.COMPANY_BOOST = float(cfg["company_boost"])
        self.CANDIDATE_MULTIPLIER = int(cfg["candidate_multiplier"])
        # 二阶段精排（2026-09-16 补做）：候选池倍数，默认 4；开关本身由
        # resolve_rerank_mode() 决定（显式参数 > 环境变量 > config > off）
        self.RERANK_POOL_MULT = int(cfg.get("rerank_pool_mult", 4) or 4)
        self.RERANK_ALPHA = float(cfg.get("rerank_alpha", 0.5) or 0.5)
        self._rag = None

    def _get_rag(self):
        """懒加载原始 RAG 模块"""
        if self._rag is None:
            sys.path.insert(0, os.path.join(
                os.path.dirname(__file__), "..", ".."
            ))
            from rag_module import RAGRetriever
            self._rag = RAGRetriever()
            self._rag.load()
        return self._rag

    def search(self, query: str, company: str = "",
               top_k: int = 5, company_mode: str = "boost",
               rerank_mode: Optional[str] = None) -> Dict:
        """
        混合检索

        参数:
          query: 搜索查询
          company: 公司名称（可选）
          top_k: 返回条数
          company_mode: company 元数据处理方式
                        "off"    不改动（与历史行为一致）
                        "boost"  命中 company 的 chunk 分数 × COMPANY_BOOST（默认）
                        "filter" 只保留命中 company 的 chunk（严格，可能丢上下文）
          rerank_mode: 二阶段精排开关（2026-09-16 补做）
                        None → 按 显式参数>env RAG_RERANK>config.retriever.rerank>off
                        "off" 不做精排（默认，行为与历史一致）
                        "ce"  取更宽候选池 → RRF → company 策略 → **cross-encoder 精排**

        返回:
          {"success": bool, "results": [...], "count": int, "rerank_mode": str}
        """
        from domain.retriever.rerank import resolve_rerank_mode
        rr_mode = resolve_rerank_mode(rerank_mode)
        try:
            rag = self._get_rag()
            results = []

            # 使用 FAISS + BM25 混合检索
            if rag.chunks_index:
                # 拼接查询（对 BM25/FAISS 召回仍有帮助，保留）
                search_query = query
                if company and company not in search_query:
                    search_query = f"{company} {search_query}"

                # 有 company 时先取更宽候选池，供后处理加权/过滤后仍能截满 top_k
                pool_k = top_k
                if company and company_mode != "off":
                    pool_k = max(top_k * self.CANDIDATE_MULTIPLIER, top_k)
                # 精排必须有更宽的候选池，否则只是对已截断的 top_k 重排（无意义）
                if rr_mode in ("ce", "ce_fuse"):
                    pool_k = max(pool_k, top_k * self.RERANK_POOL_MULT)

                chunks = rag.search_chunks_hybrid(search_query, pool_k)
                for c in chunks:
                    meta = c.get("meta", {})
                    results.append({
                        "type": "chunk",
                        "source": meta.get("source_file", ""),
                        "company": meta.get("company", ""),
                        "year": meta.get("year", ""),
                        "section": meta.get("section", ""),
                        "text": meta.get("text", "")[:500],
                        "score": c.get("score", 0),
                    })

            # company 元数据后处理
            if company and results and company_mode != "off":
                results = self._apply_company_policy(results, company, company_mode)

            # 二阶段精排：cross-encoder，作为排序的最后一步
            # （只改顺序、不改成员；任何异常 fail-open 回原顺序）
            # "ce" = 纯 CE 覆盖排序；"ce_fuse" = 与上游 RRF(含 company 加权) 加权融合
            if rr_mode in ("ce", "ce_fuse") and results:
                from domain.retriever.rerank import get_reranker
                results = get_reranker().rerank(
                    search_query, results, top_k=top_k,
                    mode=rr_mode, alpha=self.RERANK_ALPHA)

            # 截断到 top_k
            results = sorted(results, key=lambda x: -x.get("score", 0))[:top_k]

            return {
                "success": bool(results),
                "results": results,
                "count": len(results),
                "company_mode": company_mode if company else "off",
                "rerank_mode": rr_mode,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "results": [], "count": 0}

    def _apply_company_policy(self, results: List[Dict], company: str,
                              mode: str = "boost") -> List[Dict]:
        """按元数据 company 处理检索结果。

        mode="filter"：只保留命中 company 的 chunk；若一条都不命中则回退全量
                      （避免“硬过滤把结果清空”，但不掩盖——打印回退日志）。
        mode="boost" ：保留全部，命中项分数 × COMPANY_BOOST（默认）。
        """
        def hit(r):
            c = str(r.get("company") or "")
            if not c or not company:
                return False
            return c == company or company in c

        if mode == "filter":
            kept = [r for r in results if hit(r)]
            if kept:
                for r in kept:
                    r["company_matched"] = True
                return kept
            print(f"[RAG] company filter '{company}' 无命中，回退全量 "
                  f"{len(results)} 条（company 元数据 90.7% 为'行业综述'）")
            return results

        for r in results:
            if hit(r):
                r["score"] = r.get("score", 0) * self.COMPANY_BOOST
                r["company_matched"] = True
        return results

    # ==================== 查询归一化（默认路径，不调用 LLM）====================

    @staticmethod
    def normalize_query(question: str, company: str = "") -> str:
        """查询归一化：去语气词 + 补公司名 + 压空白。**纯字符串处理，零成本**。

        这是 rewrite_query 的默认行为（与历史实现逐字一致），
        命名如实反映其能力，供论文准确表述为“查询归一化”。
        """
        rewritten = question or ""
        for word in ["呢", "啊", "吧", "吗", "这个", "那个", "帮我", "请问"]:
            rewritten = rewritten.replace(word, "")
        if company and company not in rewritten:
            rewritten = f"{company} {rewritten}"
        return " ".join(rewritten.split()).strip()

    # ==================== 可选 LLM 改写（默认关闭）====================

    _LLM_REWRITE_SYSTEM = (
        "你是财报问答系统的查询改写器。把用户问题改写成一句适合检索中文券商研报的"
        "查询串：保留公司名、指标名、年份；把口语说法换成书面财务术语；删除语气词；"
        "不要回答，不要解释，只输出改写后的查询串（不超过 40 字）。"
    )

    def llm_rewrite_query(self, question: str, company: str = "") -> str:
        """真·LLM 查询改写；任何异常/超时/异常输出都回退 normalize_query。

        注意：底层 LLMClient.chat 固定 30s 超时，故开启本功能会使单次
        rag_search 的最坏延迟增加约 30s。开启前请自行权衡延迟/成本/可控性，
        并用数据论证（论文若要写“实现了 LLM 改写”，须附启用前后对比）。
        """
        base = self.normalize_query(question, company)
        try:
            from agent.llm import LLMClient
            out = LLMClient().chat(self._LLM_REWRITE_SYSTEM, base)
            if out and not str(out).startswith("[LLM Error]"):
                out = " ".join(str(out).strip().split())
                if 0 < len(out) <= 200:
                    return out
            print(f"[RAG] LLM 改写返回异常，回退归一化: {str(out)[:80]}")
        except Exception as e:
            print(f"[RAG] LLM 查询改写失败，回退归一化: {e}")
        return base

    def rewrite_query(self, question: str, company: str = "",
                       previous_queries: List[str] = None) -> str:
        """对外统一入口：按开关决定走 LLM 改写还是归一化。

        开关优先级：环境变量 RAG_LLM_REWRITE > config.yaml `rag.llm_rewrite` > 默认 false。
        默认 **关闭** —— 保持既有行为不变，避免未论证就引入延迟与不确定性。

        参数 previous_queries 保留以兼容旧签名，当前实现未使用。
        """
        if self._llm_rewrite_enabled():
            return self.llm_rewrite_query(question, company)
        return self.normalize_query(question, company)

    @staticmethod
    def _llm_rewrite_enabled() -> bool:
        """是否启用 LLM 查询改写（默认 False）。"""
        env = (os.environ.get("RAG_LLM_REWRITE") or "").strip().lower()
        if env in ("1", "true", "yes", "on"):
            return True
        if env in ("0", "false", "no", "off"):
            return False
        try:
            from agent.config import get_config
            cfg = get_config()
            cfg.load()
            return bool(cfg.get("rag.llm_rewrite", False))
        except Exception:
            return False


# 模块级默认实例
_hybrid_retriever: Optional[HybridRetriever] = None


def get_hybrid_retriever() -> HybridRetriever:
    """获取 HybridRetriever 单例"""
    global _hybrid_retriever
    if _hybrid_retriever is None:
        _hybrid_retriever = HybridRetriever()
    return _hybrid_retriever
