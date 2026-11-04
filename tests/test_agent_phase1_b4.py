# -*- coding: utf-8 -*-
"""T4 (B4) 配置加载器形同虚设 — 回归单测。

背景: agent/config.py 定义了 _init_from_yaml() 但从未调用；LLMClient 与
      sql/risk/report 三个 DB 工具全用硬编码凭据（config.py、sql_tool.py、
      risk_tool.py、report_tool.py），导致 config.yaml 形同虚设、改配置必须改代码。
修法: config.py 模块导入即调用 _init_from_yaml()；新增 get_llm_config() /
      get_database_config() 统一从 config.yaml/环境变量读取（缺失 key 打印
      可诊断日志并回退内置默认值）；LLMClient 与 3 个 DB 工具全部改走这两个入口。

本测试不连接真实 LLM / DB、不输出真实凭据：
  - 用 importlib.reload + 临时环境变量证明"模块导入即触发 _init_from_yaml"；
  - 用临时 config.yaml 验证缺失文件/缺失 key 时的可诊断日志与默认值回退；
  - 用 stub mysql.connector + 哨兵配置验证 3 个 DB 工具把连接参数交给了
    get_database_config()（若仍硬编码旧值，connect 收到的就是旧值而非哨兵值）。
  - 扫描 agent/ 与 app.py：除 config.py 默认兜底外不得含 config.yaml 里的真实凭据串。

运行(目标解释器):
    python -m pytest tests/test_agent_phase1_b4.py -q
兼容:
    python -m unittest tests.test_agent_phase1_b4 -v
"""
import contextlib
import glob
import importlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.config as agent_config
from agent.tools import report_tool, risk_tool, sql_tool


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_YAML_PATH = os.path.join(_PROJECT_ROOT, "config", "config.yaml")

# 仅供测试使用的假值 —— 绝不等于任何真实凭据
FAKE_LLM_KEY = "fake-test-api-key-not-real"
FAKE_DB_PASSWORD = "fake-test-db-password-not-real"


def _no_credential_env():
    """清空凭据相关环境变量覆盖（空串在 _apply_env_overrides 中不生效）。"""
    keys = [
        "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
        "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
    ]
    return mock.patch.dict(os.environ, {k: "" for k in keys})


def _load_real_yaml():
    import yaml
    with open(_YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestConfigInitFromYaml(unittest.TestCase):
    """B4-1: config.py 模块初始化即调用 _init_from_yaml()，get_config 真正读 YAML。"""

    def test_module_import_auto_calls_init_from_yaml(self):
        """设置 DEEPSEEK_API_KEY 环境变量后重载模块：若导入未自动执行
        _init_from_yaml()，模块常量不会带上该值（当前配置文件的 key 不同）。"""
        try:
            with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": FAKE_LLM_KEY}):
                importlib.reload(agent_config)
            self.assertEqual(
                agent_config.DEEPSEEK_API_KEY, FAKE_LLM_KEY,
                "模块导入后常量未被环境变量刷新：_init_from_yaml() 未在导入时执行",
            )
        finally:
            importlib.reload(agent_config)  # 还原真实 config/config.yaml 状态

    def test_get_config_and_getters_read_yaml_values(self):
        # 2026-09-14：.env 加载后 os.environ 里有凭据，且 ConfigLoader 单例在
        # import 期已把「env 覆盖」烘焙进缓存（_loaded=True 后不再重读 yaml）。
        # 本用例验证的是「无覆盖时 getter 读 YAML」，故需同时
        # ①置空凭据环境变量 ②丢弃单例重读，才能构造出"无覆盖"的纯净前提。
        with _no_credential_env():
            old_loader = agent_config._config_loader
            agent_config._config_loader = None
            try:
                data = _load_real_yaml()
                loader = agent_config.get_config()
                loader.load()   # 单例被丢弃后是新实例，需显式加载（惰性）
                self.assertTrue(loader._loaded)
                self.assertEqual(loader.load_error, "")
                self.assertEqual(loader.get("llm.model"), data["llm"]["model"])
                self.assertEqual(loader.get("llm.api_url"), data["llm"]["api_url"])
                self.assertEqual(
                    loader.get("database.database"), data["database"]["database"])

                llm_cfg = agent_config.get_llm_config()
                self.assertEqual(llm_cfg["api_key"], data["llm"]["api_key"])
                self.assertEqual(llm_cfg["base_url"], data["llm"]["api_url"])
                self.assertEqual(llm_cfg["model"], data["llm"]["model"])

                db_cfg = agent_config.get_database_config()
                self.assertEqual(db_cfg["host"], data["database"]["host"])
                self.assertEqual(db_cfg["port"], int(data["database"]["port"]))
                self.assertEqual(db_cfg["database"], data["database"]["database"])
                self.assertEqual(db_cfg["user"], data["database"]["user"])
                self.assertEqual(db_cfg["password"], data["database"]["password"])
            finally:
                agent_config._config_loader = old_loader

    def test_env_overrides_yaml(self):
        """契约：真实环境变量 > 配置文件。

        `.env` 亦经 os.environ 注入（见 agent/config.py `_load_dotenv_early`），
        故 `.env` 与本用例环境变量同档、都高于 config.yaml。
        用哨兵值压过真实 config.yaml，getter 必须返回哨兵 ——
        防止"配置写了不生效"这类假配置回归。
        """
        sentinel = "sentinel-env-override-value"
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": sentinel}):
            old_loader = agent_config._config_loader
            agent_config._config_loader = None
            try:
                self.assertEqual(
                    agent_config.get_llm_config()["api_key"], sentinel)
            finally:
                agent_config._config_loader = old_loader

    def test_missing_config_file_prints_diagnostic(self):
        # 需在"无凭据覆盖"前提下验证"空配置 → cfg 为空 dict"，否则
        # .env / 环境变量经 _apply_env_overrides 注入会让 cfg 非空。
        with _no_credential_env():
            with tempfile.TemporaryDirectory() as tmp:
                loader = agent_config.ConfigLoader(config_dir=tmp)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    cfg = loader.load()
                out = buf.getvalue()
                self.assertEqual(cfg, {})
                self.assertIn("[Config]", out)
                self.assertIn("config.yaml", out)
                self.assertIn("不存在", loader.load_error)
                # 空配置不得静默崩溃：get 走默认值兜底
                self.assertEqual(loader.get("llm.model", "fallback-model"),
                                 "fallback-model")

    def test_missing_key_logs_diagnostic_and_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = os.path.join(tmp, "config.yaml")
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write("# 空配置：模拟缺失全部 database.* key\n")
            old_loader = agent_config._config_loader
            try:
                agent_config._config_loader = \
                    agent_config.ConfigLoader(config_dir=tmp)
                with _no_credential_env():
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        db_cfg = agent_config.get_database_config()
                out = buf.getvalue()
                self.assertIn("[Config]", out)
                self.assertIn("database.host", out)
                self.assertIn("database.password", out)
                # 回退到内置默认值（不打印、不落盘）
                self.assertEqual(db_cfg["host"], agent_config.DB_HOST)
                self.assertEqual(db_cfg["port"], agent_config.DB_PORT)
                self.assertEqual(db_cfg["database"], agent_config.DB_NAME)
                self.assertEqual(db_cfg["user"], agent_config.DB_USER)
                self.assertEqual(db_cfg["password"], agent_config.DB_PASSWORD)
            finally:
                agent_config._config_loader = old_loader


class TestLLMAndToolsReadFromConfig(unittest.TestCase):
    """B4-2: LLMClient 与 3 个 DB 工具凭据改走 get_config()。"""

    def _install_fake_mysql(self):
        fake_connector = mock.Mock()
        fake_conn = mock.Mock()
        fake_connector.connect.return_value = fake_conn
        fake_mysql = mock.Mock()
        fake_mysql.connector = fake_connector
        modules = {"mysql": fake_mysql, "mysql.connector": fake_connector}
        return fake_connector, mock.patch.dict(sys.modules, modules)

    def test_db_tools_connection_params_come_from_config(self):
        sentinel = {
            "host": "db-sentinel-host",
            "port": 3307,
            "database": "db_sentinel_name",
            "user": "db_sentinel_user",
            "password": FAKE_DB_PASSWORD,
        }
        cases = (
            (sql_tool, sql_tool.SQLTool),
            (risk_tool, risk_tool.RiskTool),
            (report_tool, report_tool.ReportTool),
        )
        for module, tool_cls in cases:
            with self.subTest(tool=tool_cls.__name__):
                fake_connector, mysql_patch = self._install_fake_mysql()
                with mysql_patch, \
                     mock.patch.object(module, "get_database_config",
                                       return_value=dict(sentinel)):
                    conn = tool_cls()._get_conn()
                self.assertIs(conn, fake_connector.connect.return_value)
                fake_connector.connect.assert_called_once_with(
                    host=sentinel["host"], port=sentinel["port"],
                    database=sentinel["database"], user=sentinel["user"],
                    password=sentinel["password"],
                )

    def test_llmclient_reads_config_and_explicit_override_wins(self):
        conf = {
            "api_key": FAKE_LLM_KEY,
            "base_url": "https://fake-llm.example.com/v1",
            "model": "fake-model",
        }
        with mock.patch("agent.config.get_llm_config",
                        return_value=conf) as getter:
            from agent.llm import LLMClient
            client = LLMClient()
            self.assertEqual(client.api_key, FAKE_LLM_KEY)
            self.assertEqual(client.base_url, conf["base_url"])
            self.assertEqual(client.model, conf["model"])
            # 显式入参优先于配置（保持原行为）
            explicit = LLMClient(api_key="explicit-key",
                                 model="explicit-model")
            self.assertEqual(explicit.api_key, "explicit-key")
            self.assertEqual(explicit.model, "explicit-model")
            self.assertEqual(explicit.base_url, conf["base_url"])
        getter.assert_called()

    def test_no_credential_literal_in_business_code(self):
        """除 config.py 默认兜底外，agent/ 与 app.py 不含 config.yaml 真实凭据串。"""
        secrets = set()
        data = _load_real_yaml()
        secrets.add(data["llm"]["api_key"])
        secrets.add(data["database"]["password"])

        py_files = glob.glob(
            os.path.join(_PROJECT_ROOT, "agent", "**", "*.py"), recursive=True)
        py_files = [p for p in py_files if not p.endswith(os.path.join("agent", "config.py"))]
        py_files.append(os.path.join(_PROJECT_ROOT, "app.py"))

        for path in py_files:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            for secret in secrets:
                if secret and secret in src:
                    self.fail(
                        "%s 仍含 config.yaml 中的真实凭据字面量（长度 %d）"
                        % (path, len(secret)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
