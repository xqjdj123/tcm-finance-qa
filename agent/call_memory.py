# -*- coding: utf-8 -*-
"""调用记忆与去重（D2）—— args_fp 精确层 + result_fp 结果层。

边界（v1.2）：引擎不解析自然语言——result_fp 的槽位一律来自工具返回体
（D4 统一结果契约的 companies/indicators/years 字段），引擎侧不做正则/词典抽取。
query_type 不参与任何指纹（实测同一问题 auto/trend 混传、结果相同）。
"""
import json
import re
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _to_halfwidth(s: str) -> str:
    """全角标点/字母/数字 → 半角，全角空格 → 半角空格。"""
    out = []
    for ch in str(s):
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif ch == "　":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _strip_whitespace(s: str) -> str:
    return re.sub(r"\s+", "", _to_halfwidth(s))


@dataclass
class CallRecord:
    tool: str
    args_fp: str
    result_fp: Optional[str]
    slots: Dict[str, List[str]]
    result_content: str
    iteration: int
    ok: bool
    retries: int = 0


_SCHEMA_DICT = None


def _load_schema_dict() -> dict:
    """延迟加载 field_dict.SCHEMA_DICT（单一事实源），失败降级为空。"""
    global _SCHEMA_DICT
    if _SCHEMA_DICT is None:
        try:
            from field_dict import SCHEMA_DICT
            _SCHEMA_DICT = SCHEMA_DICT
        except ImportError:
            _SCHEMA_DICT = {}
    return _SCHEMA_DICT


class CallMemory:
    def __init__(self, max_retry: int = 1):
        self._by_args: Dict[tuple, CallRecord] = {}
        self._records: List[CallRecord] = []
        self._dup_count = 0
        self._max_retry = max_retry

    # ---- 参数指纹（精确层，零误杀） ----
    def fingerprint(self, tool: str, args: dict) -> str:
        cleaned = {k: v for k, v in (args or {}).items()
                   if v not in (None, "", [], {})}
        canonical = json.dumps(cleaned, sort_keys=True, ensure_ascii=False, default=str)
        normalized = _strip_whitespace(canonical)
        return hashlib.sha256(f"{tool}:{normalized}".encode("utf-8")).hexdigest()

    # ---- 结果指纹（结果层：有没有带来新信息） ----
    def result_fingerprint(self, result_data: Any) -> Optional[str]:
        if not isinstance(result_data, dict):
            return None
        companies = self._canonical_companies(result_data.get("companies"))
        indicators = self._canonical_indicators(result_data.get("indicators"))
        years = self._norm_years(result_data.get("years"))
        data_sig = self._data_signature(result_data.get("data"))

        # 非契约工具（无槽位字段）：退化到 data 结构/行数哈希；再退化 None
        if not companies and not indicators and not years:
            if data_sig is None:
                return None
            return "fallback:" + data_sig

        parts = {"companies": companies, "indicators": indicators,
                 "years": years, "data": data_sig}
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _canonical_companies(self, companies) -> List[str]:
        from domain.normalize.company_names import canonical as canon_company
        out = set()
        for c in (companies or []):
            cc = canon_company(c)
            if cc:
                out.add(cc)
        return sorted(out)

    def _canonical_indicators(self, indicators) -> List[str]:
        schema = _load_schema_dict()
        out = set()
        for ind in (indicators or []):
            s = str(ind).strip()
            if not s:
                continue
            if s in schema:  # 英文字段名直接命中
                out.add(s)
                continue
            for en, info in schema.items():
                if s in info.get("synonyms", []):  # 中文同义词 → en_name
                    out.add(en)
                    break
            else:
                out.add(re.sub(r"\s+", "", s))  # 未命中：去空白原样
        return sorted(out)

    def _norm_years(self, years) -> List[str]:
        out = set()
        for y in (years or []):
            s = str(y).strip()
            if s:
                out.add(s)
        return sorted(out)

    def _data_signature(self, data) -> Optional[str]:
        if data is None:
            return None
        if not isinstance(data, list):
            data = [data]
        if not data:
            return None
        rows = []
        for row in data:
            if isinstance(row, dict):
                rows.append(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str))
            else:
                rows.append(str(row))
        rows.sort()  # 行序不敏感
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()

    # ---- 记忆读写 ----
    def lookup(self, tool: str, args: dict) -> Optional[CallRecord]:
        return self._by_args.get((tool, self.fingerprint(tool, args)))

    def record(self, tool: str, args: dict, result_data: Any,
               result_content: str, iteration: int) -> CallRecord:
        fp = self.fingerprint(tool, args)
        ok = bool(result_data.get("success")) if isinstance(result_data, dict) else False
        prev = self._by_args.get((tool, fp))
        retries = (prev.retries + 1) if prev is not None else 0
        rec = CallRecord(
            tool=tool,
            args_fp=fp,
            result_fp=self.result_fingerprint(result_data),
            slots=self._extract_slots(result_data),
            result_content=result_content,
            iteration=iteration,
            ok=ok,
            retries=retries,
        )
        self._by_args[(tool, fp)] = rec
        self._records.append(rec)
        return rec

    def _extract_slots(self, result_data) -> Dict[str, List[str]]:
        if not isinstance(result_data, dict):
            return {}
        return {
            "companies": list(result_data.get("companies") or []),
            "indicators": list(result_data.get("indicators") or []),
            "years": list(result_data.get("years") or []),
        }

    # ---- 供 Evaluator / engine 使用 ----
    def can_retry(self, rec: CallRecord) -> bool:
        """失败记录是否还允许再执行一次（retries < max_retry）。"""
        return rec.retries < self._max_retry

    def mark_dup(self) -> None:
        self._dup_count += 1

    def repeated_count(self) -> int:
        return self._dup_count

    def has_success(self) -> bool:
        return any(r.ok for r in self._records)
