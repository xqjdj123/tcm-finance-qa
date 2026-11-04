# -*- coding: utf-8 -*-
"""二阶段精排（cross-encoder rerank）—— 供 domain/retriever/hybrid.py 在 RRF 融合后调用。

命名如实（2026-09-16 补做，写论文前务必读这段）
------------------------------------------------
开题报告承诺「混合检索 + **利用重排序模型进行精排**」。复核历史实现：

  · `rag_module.py:539-566` 有 `_rerank_bge` / `search_with_rerank`，但
    - 形态是 **bge-m3 双编码器余弦重排**（query 与 passage 分别编码再算余弦），
      本质仍是 bi-encoder，且与 FAISS 那一路**同源同模型**，并非 cross-encoder；
    - 全仓库**零外部调用**（仅自身定义处出现），agent 主路径
      `rag_tool → hybrid.search → search_chunks_hybrid` 完全绕开它。
  · 故开题承诺的「重排序模型精排」**未交付**。

本模块补做**真 cross-encoder 精排**：对 (query, passage) **联合编码**后输出相关性
logit，与 bi-encoder 的「分别编码再算余弦」有本质区别（交互式编码 vs 独立编码）。

模型与获取方式
--------------
  · BAAI/bge-reranker-base（XLMRobertaForSequenceClassification, num_labels=1）
  · 本机 huggingface.co / hf-mirror 均不可达，经 **ModelScope** 获取，
    落盘 `models/reranker_bge_base/`（约 1.11GB，已在 .gitignore 内）
  · **不走 sentence_transformers**：本机旧版 ST 与 huggingface_hub 不兼容
    （`cached_download` 已被移除），直接用 transformers 原生加载，依赖更少。

设计约束（与项目既有风格一致）
------------------------------
  · **懒加载 + 单例**：首次调用才加载（约 5s / 1.1GB），不拖慢默认路径
  · **fail-open**：缺模型 / 缺依赖 / 任何异常 → 返回原顺序并打印一行，绝不阻断检索
  · **默认关闭**：开关来自 `retriever.rerank`，配置不变则行为不变
"""
import math
import os
import time
from typing import Dict, List, Optional

# 项目根：本文件位于 <root>/domain/retriever/rerank.py
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DEFAULT_MODEL_PATH = os.path.join("models", "reranker_bge_base")
_REQUIRED_FILES = ("config.json", "model.safetensors")
_ENV_RERANK = "RAG_RERANK"


def _retriever_cfg() -> dict:
    """读取检索层配置；失败退回内置默认（与 hybrid.py 同款兜底）。"""
    try:
        from agent.config import get_retriever_config
        return get_retriever_config() or {}
    except Exception:
        return {}


def resolve_rerank_mode(explicit: Optional[str] = None) -> str:
    """确定精排开关，返回 "off" 或 "ce"。

    优先级：显式参数 > 环境变量 `RAG_RERANK` > config `retriever.rerank` > "off"。
    只认这两个值（大写/带空格/1|true|on 均归一化）。
    """
    def _norm(v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s in ("ce_fuse", "ce-fuse", "fuse", "ce+rrf"):
            return "ce_fuse"
        if s in ("ce", "cross-encoder", "cross_encoder", "rerank", "on", "1", "true", "yes"):
            return "ce"
        if s in ("off", "0", "false", "no", "none", ""):
            return "off"
        return None

    for src in (explicit, os.environ.get(_ENV_RERANK),
                _retriever_cfg().get("rerank")):
        got = _norm(src)
        if got:
            return got
    return "off"


class CrossEncoderReranker:
    """cross-encoder 精排器（懒加载 + fail-open）。

    用法：
        reranker = get_reranker()
        ranked = reranker.rerank(query, items, top_k=5)   # 异常时返回 items[:top_k]
    """

    def __init__(self, model_path: Optional[str] = None, max_length: int = 512,
                 batch_size: int = 8):
        cfg = _retriever_cfg()
        rel = model_path or cfg.get("rerank_model_path") or _DEFAULT_MODEL_PATH
        self.model_path = rel if os.path.isabs(rel) else os.path.join(_BASE_DIR, rel)
        self.max_length = int(max_length or cfg.get("rerank_max_length") or 512)
        self.batch_size = int(batch_size or cfg.get("rerank_batch_size") or 8)

        self._tok = None
        self._model = None
        self.load_error: Optional[str] = None

        # 观测字段（供 A/B 实验汇报代价）
        self.calls = 0
        self.total_elapsed = 0.0
        self.load_elapsed = 0.0

    # ---------------- 可用性 ----------------

    def available(self) -> bool:
        """模型文件是否齐备（不触发加载）。"""
        if not os.path.isdir(self.model_path):
            return False
        return all(os.path.isfile(os.path.join(self.model_path, f))
                   for f in _REQUIRED_FILES)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self) -> bool:
        """懒加载；失败记录原因并返回 False（不改抛）。"""
        if self._model is not None:
            return True
        if not self.available():
            self.load_error = "模型目录缺失或不完整: %s" % self.model_path
            return False
        try:
            import torch  # noqa: F401
            from transformers import (AutoModelForSequenceClassification,
                                      AutoTokenizer)
        except Exception as e:  # pragma: no cover - 环境相关
            self.load_error = "缺少依赖: %r" % (e,)
            return False
        try:
            t0 = time.time()
            self._tok = AutoTokenizer.from_pretrained(self.model_path)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_path)
            self._model.eval()
            self.load_elapsed = time.time() - t0
            print("[rerank] cross-encoder 已加载 %s (%.1fs)" % (
                os.path.basename(self.model_path), self.load_elapsed))
            return True
        except Exception as e:  # pragma: no cover - 环境相关
            self.load_error = "加载失败: %r" % (e,)
            self._model = None
            return False

    # ---------------- 打分与重排 ----------------

    def score(self, query: str, passages: List[str]) -> Optional[List[float]]:
        """对 (query, passage) 逐对打分，返回 logits；任何异常返回 None。"""
        if not passages:
            return []
        if not self._ensure_loaded():
            return None
        try:
            import torch
            t0 = time.time()
            with torch.no_grad():
                inputs = self._tok([[query, p or ""] for p in passages],
                                   padding=True, truncation=True,
                                   max_length=self.max_length,
                                   return_tensors="pt")
                logits = self._model(**inputs).logits.view(-1).float().tolist()
            self.calls += 1
            self.total_elapsed += time.time() - t0
            return logits
        except Exception as e:
            print("[rerank] 打分失败，回退原顺序: %r" % (e,))
            return None

    def rerank(self, query: str, items: List[Dict], top_k: Optional[int] = None,
               text_key: str = "text", mode: str = "ce",
               alpha: float = 0.5) -> List[Dict]:
        """按 cross-encoder 分数重排 items（fail-open：异常即返回原序前 top_k）。

        mode:
          "ce"      纯 CE 覆盖排序（最终序 = CE 分序）
          "ce_fuse" **加权融合**：alpha×归一化(RRF分) + (1-alpha)×sigmoid(CE分)
                    用于缓解"纯 CE 挤掉 company 等上游信号"的副作用

        · 保留原 `score` 到 `rrf_score`；CE 原始 logit 写入 `rerank_score`
        · 不改变成员集合，只改顺序（外加末尾截断到 top_k）
        """
        n = len(items)
        k = top_k if top_k is not None else n
        if n == 0:
            return items
        scores = self.score(query, [str(it.get(text_key) or "") for it in items])
        if not scores or len(scores) != n:
            return items[:k]

        ce_norm = [1.0 / (1.0 + math.exp(-float(s))) for s in scores]
        if mode == "ce_fuse":
            base = [float(it.get("score") or 0.0) for it in items]
            lo, hi = min(base), max(base)
            span = (hi - lo) or 1.0
            base_norm = [(b - lo) / span for b in base]
            final = [alpha * b + (1.0 - alpha) * c
                     for b, c in zip(base_norm, ce_norm)]
        else:
            final = [float(s) for s in scores]

        out = []
        for it, sc, fn in zip(items, scores, final):
            new_it = dict(it)
            if "rrf_score" not in new_it:
                new_it["rrf_score"] = new_it.get("score")
            new_it["rerank_score"] = round(float(sc), 4)
            new_it["fused_score"] = round(float(fn), 4) if mode == "ce_fuse" else None
            new_it["score"] = round(float(fn), 4)
            out.append(new_it)
        out.sort(key=lambda x: -x.get("score", 0.0))
        return out[:k]


# ---------------- 模块级单例 ----------------

_reranker: Optional[CrossEncoderReranker] = None


def get_reranker() -> CrossEncoderReranker:
    """获取精排器单例（懒加载，模型在首次打分时才真正载入）。"""
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoderReranker()
    return _reranker
