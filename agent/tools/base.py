# -*- coding: utf-8 -*-
"""工具基类"""


class BaseTool:
    name = ""
    description = ""

    def run(self, inputs: dict) -> dict:
        raise NotImplementedError

    def to_schema(self) -> dict:
        """返回工具的JSON Schema，供LLM理解"""
        return {
            "name": self.name,
            "description": self.description,
        }
