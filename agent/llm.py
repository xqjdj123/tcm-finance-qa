# -*- coding: utf-8 -*-
"""DeepSeek API 客户端 — 升级版：支持 Function Calling + 流式 + 重试"""
import json
import time
import requests
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ============================================================
# 数据类型
# ============================================================

@dataclass
class ToolCall:
    """LLM 返回的工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: Optional[str] = None        # 文本内容（无工具调用时）
    tool_calls: List[ToolCall] = field(default_factory=list)  # 工具调用列表
    finish_reason: str = "stop"          # stop | tool_calls | length
    usage: Dict[str, int] = field(default_factory=dict)  # token 用量
    model: str = ""
    reasoning_content: Optional[str] = None  # DeepSeek 特有：思考内容


# ============================================================
# LLM 客户端
# ============================================================

class LLMClient:
    """DeepSeek LLM 客户端 — 支持 Function Calling"""

    # 类级默认值：即使 object.__new__ 绕过 __init__（既有测试用），属性仍可用。
    # __init__ 会用 config/config.yaml 的值覆盖它们（"配置为准，常量兜底"）。
    temperature = 0.1
    max_tokens = 2048
    timeout = 30          # 纯文本调用超时（秒）
    timeout_tools = 60    # Function Calling 调用超时（秒）
    max_retries = 2

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        from agent.config import get_llm_config, get_llm_runtime_config
        conf = get_llm_config()
        self.api_key = api_key or conf["api_key"]
        self.base_url = base_url or conf["base_url"]
        self.model = model or conf["model"]
        # 运行参数接线（2026-09）：原为代码内联字面量，现读 config.yaml
        rt = get_llm_runtime_config()
        self.temperature = float(rt["temperature"])
        self.max_tokens = int(rt["max_tokens"])
        self.timeout = int(rt["timeout"])
        self.max_retries = int(rt["max_retries"])
        # timeout_tools 走配置；缺 key 回退类级默认 60（与接线前一致）
        try:
            from agent.config import get_config
            v = get_config().get("llm.timeout_tools")
            self.timeout_tools = int(v) if v is not None else 60
        except Exception:
            self.timeout_tools = 60
        self.messages: List[Dict] = []  # 多轮对话历史

    def _resolve_temperature(self, temperature):
        """未显式传温度时用配置默认值（显式传参优先，兼容旧调用）。"""
        return self.temperature if temperature is None else temperature

    # ========== 基础调用 ==========

    def chat(self, system_prompt: str, user_message: str,
             temperature: float = None) -> str:
        """单轮对话（文本返回，兼容旧接口）

        temperature=None 时用 config.yaml `llm.temperature`。
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        return self._call_api(messages, self._resolve_temperature(temperature))

    def chat_with_history(self, user_message: str,
                          temperature: float = None) -> str:
        """多轮对话，自动维护 history"""
        self.messages.append({"role": "user", "content": user_message})
        response = self._call_api(self.messages, self._resolve_temperature(temperature))
        self.messages.append({"role": "assistant", "content": response})
        return response

    def reset(self):
        """重置多轮对话"""
        self.messages = []

    # ========== Function Calling (新增) ==========

    def chat_with_tools(self, messages: List[Dict],
                         tools: List[Dict],
                         tool_choice: str = "auto",
                         temperature: float = 0.1) -> LLMResponse:
        """
        带工具调用的 LLM 请求

        参数:
          messages: 完整的消息列表（system + user + assistant + tool）
          tools: OpenAI 格式的工具 Schema 列表
          tool_choice: "auto" | "none" | "required" | 特定工具

        返回:
          LLMResponse: 包含 content 或 tool_calls
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self._resolve_temperature(temperature),
            "max_tokens": self.max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        return self._call_api_with_tools(payload)

    def _call_api_with_tools(self, payload: dict) -> LLMResponse:
        """
        执行 API 调用并解析 Function Calling 结果
        带指数退避重试
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

                if resp.status_code in (429, 500, 502, 503) and attempt < max_retries:
                    wait = 2 ** attempt
                    print(f"  [LLM] {resp.status_code} 重试 ({attempt+1}/{max_retries})，等待 {wait}s")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                choice = data["choices"][0]
                message = choice.get("message", {})
                finish_reason = choice.get("finish_reason", "stop")

                # 解析工具调用
                tool_calls = []
                raw_tool_calls = message.get("tool_calls", [])
                for tc in raw_tool_calls:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        args = {"raw_arguments": args_str}
                    tool_calls.append(ToolCall(
                        id=tc.get("id", ""),
                        name=func.get("name", ""),
                        arguments=args,
                    ))

                return LLMResponse(
                    content=message.get("content"),
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    usage=data.get("usage", {}),
                    model=data.get("model", self.model),
                    reasoning_content=message.get("reasoning_content"),
                )

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    print(f"  [LLM] Timeout 重试 ({attempt+1}/{max_retries})")
                    continue
                return LLMResponse(content="[LLM Error] 请求超时")

            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    print(f"  [LLM] 请求错误: {e}，重试 ({attempt+1}/{max_retries})")
                    time.sleep(1)
                    continue
                return LLMResponse(content=f"[LLM Error] {e}")

            except Exception as e:
                return LLMResponse(content=f"[LLM Error] {e}")

        return LLMResponse(content="[LLM Error] 重试次数用尽")

    # ========== 底层 API 调用（兼容旧接口） ==========

    def _call_api(self, messages: List[Dict],
                  temperature: float = None) -> str:
        """纯文本 API 调用（兼容旧接口）"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self._resolve_temperature(temperature),
            "max_tokens": self.max_tokens,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[LLM Error] {e}"
