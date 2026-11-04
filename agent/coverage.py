# -*- coding: utf-8 -*-
"""覆盖审计器（CoverageAuditor）—— 收敛前独立审计"用户问的子问题，答到了吗"。

组件编号 **D10**（勿与 D4 混）：D1–D9 见 `doc/Agent优化-设计文档.md`
（D1 Evaluator / D2 CallMemory / D3 Recovery / **D4 统一结果契约** / D5 bootstrap /
D6 MessageBuilder / D7 执行预算 / D8 Metrics / D9 ResultGovernor）；
D10 覆盖审计为 CoverageAuditor 方案（阶段2）**新增**的组件。

【为什么需要它：接的是自己预留的钩子，不是外来架构】
  agent/evaluator.py 是【规则层】：只读工具返回体（success/data）与 CallMemory，
  判"够了"（sufficient / force_answer），它无法判"缺什么"——evaluator docstring
  自陈"'缺什么'需要语义，交给二期 LLM"。本模块就是那"二期"。
  阶段1 实测（覆盖率尺子 n=3：有效覆盖率 0.462→0.452，无可测提升）证明：
  漏答根因**不在**"模型不知道该答全"（Prompt 软约束已加、无效），而在
  **收敛/输出路径**（force_answer:soft_limit 截断、工具结果综合漏项）
  → 需要独立的"验收"环节，而非继续加强"规划"。

【职责边界（单一）】
  只做一件事：给定「用户问题 + 已执行工具摘要 + 候选答案」，判"哪些子问题没答到"。
  不重排、不重查、不改写答案：只产出 missing 清单 + 补救指引，由引擎决定打回与否。
  fail-open：解析失败/异常一律 ok=False，引擎按"无审计"继续正常收敛——
  审计器自身故障**绝不**拖垮主链路。

【两种"未答"必须区分（整个设计命门）】
  looked_not_answered  数据已在上方工具结果中、只是没写进答案
                       → 引导【直接补答，勿重查】。若不区分、一律叫"补查"，
                         模型会重查同参 → 被 CallMemory 拦 → no_new_info 强制收口
                         → 答案照样不全（这正是 B2040/B2042 的病灶形态）。
  not_looked           压根没查 → 引导【先调工具取数再答】。

【与 Evaluation.missing 的关系】
  Evaluation.missing 字段（evaluator.py:16 声明、此前全仓无读写方）由本模块填充：
  to_evaluation() 把未答清单写进该字段——"二期"在代码里就此闭环。
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.evaluator import Evaluation


AUDITOR_SYSTEM = """你是一个严格的“答案覆盖审计员”。用户提了一个包含若干子问题的问题，
系统已执行了一些查询并给出了候选答案。你的唯一任务是：找出候选答案【没有真正回应】的子问题。

判定规则（严格遵守）：
0. 未答项必须来自“用户问题”里明确问到的子问题/字段/对象；用户没问的字段、概念一律不得列为未答。
1. 只依据给定的“候选答案”文本判断：不要脑补，不要替它作答。
2. 某个子问题在候选答案里有明确的结论 / 数据 / 名单 / 分析 → 视为已答，不要列入 missing。
3. 候选答案完全没提到该子问题，或只是在重复问题、或输出的其实是思考过程 / 工具调用标记
   而非结论 → 视为未答，列入 missing。
4. 对每个未答子问题判定类别：
   - "looked_not_answered"：所需数据【已经出现在上方“已执行工具摘要/结果”里】
     （系统查到了、但没写进答案）；
   - "not_looked"：所需数据【还没有被任何工具查询过】。
   判断依据：摘要里有没有与该子问题相关、且 success 的查询。
5. 不要因为措辞不同 / 啰嗦 / 格式差异而判未答。
6. 若所有子问题都已答到，missing 返回空数组。

只输出如下 JSON，不要任何额外文字或代码块标记：
{"missing":[{"text":"未答的子问题（<=30字）","status":"looked_not_answered"}]}"""


def _extract_json(text: str) -> dict:
    """从 LLM 输出里抠出第一个 JSON 对象（容忍 ```json 围栏与前后杂文本）。"""
    if not text:
        raise ValueError("审计器返回空")
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("审计器输出中找不到 JSON: %s" % text[:120])
    return json.loads(t[i:j + 1])


@dataclass
class CoverageAudit:
    """一次审计的结果。

    ok      : 审计是否**成功执行**（False = 解析失败/异常 → 引擎 fail-open）
    missing : [{"text": 未答子问题, "status": looked_not_answered|not_looked}]
    advice  : 注入主循环的补救指引（已按两种 status 分别措辞）
    raw     : LLM 原始输出（留档 / 诊断）
    error   : 失败原因（ok=False 时有值）
    """
    ok: bool = False
    missing: List[Dict[str, str]] = field(default_factory=list)
    advice: str = ""
    raw: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        """审计成功且无未答子问题。"""
        return self.ok and not self.missing

    def to_evaluation(self) -> Evaluation:
        """把未答清单写进沉睡的 Evaluation.missing（二期闭环）。"""
        texts = [m["text"] for m in self.missing if m.get("text")]
        return Evaluation(sufficient=not texts, force_answer=False,
                          reason="coverage_audit", missing=texts)


class CoverageAuditor:
    """收敛前覆盖审计器（独立于 Evaluator 的规则层）。"""

    def __init__(self, llm_client, max_rounds: int = 1,
                 min_tool_calls: int = 1, max_answer_chars: int = 1500):
        """
        参数:
          llm_client      : 复用引擎的 LLM 客户端（LLMClient.chat，temp=0）
          max_rounds      : 单次运行最多审计打回次数（默认 1 → 防死循环）
          min_tool_calls  : 至少发生过多少次非 dup 工具调用才值得审计（0=总是）
          max_answer_chars: 候选答案入审计 Prompt 的截断长度
        """
        self.llm = llm_client
        self.max_rounds = max_rounds
        self.min_tool_calls = min_tool_calls
        self.max_answer_chars = max_answer_chars

    # ---------------- 决策：是否值得审计 ----------------
    def should_audit(self, *, force_answer: bool, audit_rounds: int,
                     tool_calls_log: List[Dict]) -> bool:
        """三个硬门：已到打回上限 / 没有足够工具调用 → 不值得；其余返回 True。

        注意：force_answer **不在此拦截**——强制收口路径仍需审计以便"只记录"，
        由调用方据 force_answer 决定"记录还是打回"（Evaluator 一票收口权不可被架空）。
        """
        if audit_rounds >= self.max_rounds:
            return False
        executed = [tc for tc in (tool_calls_log or []) if not tc.get("dup")]
        return len(executed) >= self.min_tool_calls

    # ---------------- 执行一次审计 ----------------
    def audit(self, question: str, tool_calls_log: List[Dict],
              answer: str) -> CoverageAudit:
        """审计候选答案对子问题的覆盖。任何异常都降级为 ok=False（fail-open）。"""
        try:
            user = self._build_user(question, tool_calls_log, answer)
            raw = self.llm.chat(AUDITOR_SYSTEM, user, temperature=0.0)
            if not raw or str(raw).strip().startswith("[LLM Error]"):
                return CoverageAudit(ok=False, raw=str(raw or ""),
                                     error="审计 LLM 返回异常: %s" % str(raw)[:120])
            data = _extract_json(raw)
            missing: List[Dict[str, str]] = []
            for item in (data.get("missing") or []):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                status = str(item.get("status") or "").strip()
                if status not in ("looked_not_answered", "not_looked"):
                    # 未知状态按"未查"处理最保守：引导先查（查错了也无害），
                    # 避免误标为"已查未答"导致模型不敢查、凭空作答。
                    status = "not_looked"
                missing.append({"text": text[:60], "status": status})
            return CoverageAudit(ok=True, missing=missing, raw=str(raw),
                                 advice=self._render_advice(missing))
        except Exception as e:  # noqa: BLE001 —— fail-open 是本模块的核心契约
            return CoverageAudit(ok=False, error=str(e)[:200])

    # ---------------- 内部：构造审计输入 ----------------
    def _build_user(self, question: str, tool_calls_log: List[Dict],
                    answer: str) -> str:
        lines = ["用户问题：", str(question or "").strip(), "",
                 "已执行工具摘要（按调用顺序）："]
        n = 0
        for tc in (tool_calls_log or []):
            n += 1
            tool = tc.get("tool", "?")
            args = tc.get("arguments", {})
            if isinstance(args, dict):
                args_s = "、".join("%s=%s" % (k, str(v)[:40])
                                   for k, v in args.items())
            else:
                args_s = str(args)[:120]
            rp = tc.get("result_parsed")
            status = "?"
            if isinstance(rp, dict):
                if rp.get("success"):
                    status = "success"
                    data = rp.get("data")
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        keys = list(data[0].keys())[:12]
                        status += " | 返回列: " + "/".join(str(k) for k in keys)
                else:
                    status = "失败: " + str(rp.get("error", ""))[:60]
            lines.append("  %d) %s(%s) → %s" % (n, tool, args_s, status))
        if n == 0:
            lines.append("  （无）")
        lines.append("")
        lines.append("候选答案：")
        lines.append((answer or "（空）")[: self.max_answer_chars])
        lines.append("")
        lines.append("请输出未答子问题的 JSON。")
        return "\n".join(lines)

    # ---------------- 内部：构造补救指引（区分两种"未答"）----------------
    @staticmethod
    def _render_advice(missing: List[Dict[str, str]]) -> str:
        not_looked = [m["text"] for m in missing
                      if m.get("status") != "looked_not_answered"]
        looked = [m["text"] for m in missing
                  if m.get("status") == "looked_not_answered"]
        parts = ["[覆盖审计：你上一次给出的答案并未回应下列子问题，"
                 "请补齐后重新给出【完整答案】。]"]
        if not_looked:
            parts.append("以下子问题【尚未查询】，请先调用合适工具获取数据再作答：\n"
                         + "\n".join("- " + t for t in not_looked))
        if looked:
            parts.append("以下子问题所需数据【已在上方工具结果中】，"
                         "请直接据此作答，**不要重新查询**：\n"
                         + "\n".join("- " + t for t in looked))
        parts.append("最终答案必须逐条覆盖上述子问题，每条都要有明确结论。")
        return "\n\n".join(parts)
