# -*- coding: utf-8 -*-
"""D5 工具注册自举 — ensure_tools_registered 在 registry 为空时自动注册。

背景: 引擎 __init__ 此前只 get_tool_registry() 不注册，绕过 app.py 直接
      get_react_agent() 会拿到 0 工具（静默故障）。修法: ensure_tools_registered()
      在 registry 为空时幂等调用 register_all_tools()。

运行:
    python -m pytest tests/test_agent_bootstrap.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools import registry as reg_mod


class TestToolsBootstrap(unittest.TestCase):
    """D5: 冷启动下 registry 为空，ensure_tools_registered 应自动补齐工具。"""

    def test_ensure_registers_when_empty(self):
        """registry 为空 → ensure_tools_registered 自动注册，且含核心工具。"""
        reg_mod._registry = None  # 模拟"无任何入口注册过工具"的冷启动
        r = reg_mod.ensure_tools_registered()
        self.assertGreaterEqual(
            len(r), 1,
            "registry 为空时 ensure_tools_registered 应自动注册工具（D5）",
        )
        self.assertIn("sql_query", r.list_tools(),
                      "核心查数工具 sql_query 应已注册")

    def test_ensure_idempotent(self):
        """非空 registry 再次调用应直接返回、不重复注册。"""
        reg_mod._registry = None
        r1 = reg_mod.ensure_tools_registered()
        n1 = len(r1)
        r2 = reg_mod.ensure_tools_registered()
        self.assertIs(r1, r2, "ensure_tools_registered 应始终返回同一单例")
        self.assertEqual(len(r2), n1, "二次调用应幂等，不重复注册")


if __name__ == "__main__":
    unittest.main(verbosity=2)
