# -*- coding: utf-8 -*-
"""DeepSeek API客户端"""
import requests
import json
from agent.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


class LLMClient:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_BASE_URL
        self.model = DEEPSEEK_MODEL
        self.messages = []

    def chat(self, system_prompt, user_message, temperature=0.1):
        """单轮对话"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        return self._call_api(messages, temperature)

    def chat_with_history(self, user_message, temperature=0.1):
        """多轮对话，自动维护history"""
        self.messages.append({"role": "user", "content": user_message})
        response = self._call_api(self.messages, temperature)
        self.messages.append({"role": "assistant", "content": response})
        return response

    def reset(self):
        self.messages = []

    def _call_api(self, messages, temperature=0.1):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 2048,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[LLM Error] {e}"
