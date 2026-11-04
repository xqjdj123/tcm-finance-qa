# -*- coding: utf-8 -*-
"""Agent 配置 — 从 YAML 加载，环境变量覆盖

凭据/连接参数的唯一业务来源是环境变量（DB_* / DEEPSEEK_*，可写在仓库根 .env）
或 config/config.yaml（**不含明文凭据的模板见 config/config.example.yaml**）。
本文件模块级默认值仅用于全部来源都缺失时的兜底，并在缺失时打印可诊断日志；
业务代码（llm.py、sql/risk/report 工具）一律经 get_llm_config() / get_database_config()
读取，禁止再内联真实凭据字面量。

配置优先级（高 → 低）：
    真实环境变量  >  .env 文件  >  config/config.yaml  >  本文件内置默认值
"""

import os


def _load_dotenv_early() -> str:
    """在读取 os.environ 之前加载仓库根目录的 .env（返回实际加载的路径，空串=未加载）。

    设计要点：
    - **必须在本模块任何 `os.environ.get(...)` 之前调用**：本文件模块级常量
      （DEEPSEEK_API_KEY / DB_PASSWORD）在 import 时即求值，晚于此处就读不到 .env。
    - 优先使用 python-dotenv（若已安装）；**未安装时回退到内置极简解析器**，
      因此 .env 功能不构成新的硬依赖（requirements.txt 里 dotenv 标为可选）。
    - `override=False`：真实环境变量优先于 .env，便于 CI / 临时注入覆盖本地文件。
    - 找不到 .env 时静默返回（本地未创建 .env 是常态，不应报错或刷屏）。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return ""

    try:  # 首选：python-dotenv（正确处理引号 / 转义 / export 前缀）
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path, override=False)
        return env_path
    except Exception:
        pass

    # 回退：内置极简解析器（KEY=VALUE，支持 # 注释与成对引号；不覆盖已有环境变量）
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
        return env_path
    except Exception:
        return ""


# 模块导入即加载 .env —— 必须早于下方所有 os.environ.get(...)
DOTENV_PATH = _load_dotenv_early()


# ============================================================
# 默认值（全部来源缺失时的兜底；真实值由环境变量 / config.yaml 提供）
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
MAX_STEPS = 8
# Agent 层一期配置默认值（YAML 缺失/漏 key 时兜底）
SOFT_ITERATION_LIMIT = 5
CALL_DEDUP = True
FAILED_CALL_MAX_RETRY = 1
EVALUATOR_ENABLED = True

DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "finance_data"
DB_USER = "root"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


# ============================================================
# YAML 配置加载器
# ============================================================

class ConfigLoader:
    """
    RQagentPro 风格配置加载器
    支持 YAML + dot-notation 访问 + 环境变量覆盖
    """

    def __init__(self, config_dir: str = None):
        if config_dir is None:
            # 默认路径: codex/config/
            config_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config"
            )
        self.config_dir = config_dir
        self._config: dict = {}
        self._loaded = False
        self.load_error: str = ""  # 最近一次加载失败原因（空串=成功），供诊断

    def load(self) -> dict:
        """加载配置并返回；缺失/解析失败打印可诊断错误并回退默认值。"""
        if self._loaded:
            return self._config

        import yaml
        config_path = os.path.join(self.config_dir, "config.yaml")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                parsed = yaml.safe_load(f)
            self._config = parsed or {}
            self._loaded = True
            self.load_error = ""
            print(f"[Config] 已加载配置: {config_path}")
        except FileNotFoundError:
            self._config = {}
            self._loaded = True
            self.load_error = f"配置文件不存在: {config_path}"
            print(f"[Config] 错误: {self.load_error}")
            print("[Config] 已回退到内置默认值。若连接不上真实服务，请先补全该文件，"
                  "或通过环境变量 DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/"
                  "DEEPSEEK_API_KEY/DEEPSEEK_MODEL 覆盖后重启。")
        except Exception as e:
            self._config = {}
            self._loaded = True
            self.load_error = f"配置文件加载失败: {config_path}: {e}"
            print(f"[Config] 错误: {self.load_error}")
            print("[Config] 已回退到内置默认值，请修正配置后重启。")

        # 环境变量覆盖
        self._apply_env_overrides()

        return self._config

    def _apply_env_overrides(self):
        """环境变量覆盖 YAML 配置"""
        overrides = {
            "DEEPSEEK_API_KEY": "llm.api_key",
            "DEEPSEEK_MODEL": "llm.model",
            "DB_HOST": "database.host",
            "DB_PORT": "database.port",
            "DB_NAME": "database.database",
            "DB_USER": "database.user",
            "DB_PASSWORD": "database.password",
            "PORT": "server.port",
            "DEBUG": "server.debug",
        }
        for env_var, config_path in overrides.items():
            val = os.environ.get(env_var)
            if val:
                self.set(config_path, val)

    def get(self, key: str, default=None):
        """
        dot-notation 访问配置
        例: cfg.get("llm.model") → "deepseek-chat"
        """
        if not self._loaded:
            self.load()

        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value

    def set(self, key: str, value):
        """设置配置值"""
        if not self._loaded:
            self.load()

        keys = key.split(".")
        target = self._config
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value


# ============================================================
# 全局单例 + 业务读取入口
# ============================================================
_config_loader: ConfigLoader = None
_warned_keys: set = set()


def get_config() -> ConfigLoader:
    """获取全局配置加载器"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


def _warn_missing(keys) -> None:
    """打印缺失 key 诊断（同 key 进程内只提醒一次，避免刷屏）。"""
    for key in keys:
        if key in _warned_keys:
            continue
        _warned_keys.add(key)
        print(f"[Config] 警告: 配置缺少 {key}，已回退到内置默认值。"
              "请检查 config/config.yaml 对应字段或用环境变量覆盖；改配置无需改代码。")


def get_llm_config() -> dict:
    """LLM 连接配置：YAML -> 环境变量 -> 内置默认值（key 缺失时打印诊断日志）。"""
    cfg = get_config()
    cfg.load()
    mapping = (
        ("api_key", "llm.api_key", DEEPSEEK_API_KEY),
        ("base_url", "llm.api_url", DEEPSEEK_BASE_URL),
        ("model", "llm.model", DEEPSEEK_MODEL),
    )
    result, missing = {}, []
    for field, key, default in mapping:
        value = cfg.get(key)
        if value is None:
            missing.append(key)
            value = default
        result[field] = value
    if missing:
        _warn_missing(missing)
    return result


def get_database_config() -> dict:
    """数据库连接配置：YAML -> 环境变量 -> 内置默认值（key 缺失时打印诊断日志）。"""
    cfg = get_config()
    cfg.load()
    mapping = (
        ("host", "database.host", DB_HOST),
        ("port", "database.port", DB_PORT),
        ("database", "database.database", DB_NAME),
        ("user", "database.user", DB_USER),
        ("password", "database.password", DB_PASSWORD),
    )
    result, missing = {}, []
    for field, key, default in mapping:
        value = cfg.get(key)
        if value is None:
            missing.append(key)
            value = default
        if field == "port":
            value = int(value)
        result[field] = value
    if missing:
        _warn_missing(missing)
    return result


# ============================================================
# 业务子系统参数（2026-09 接线：消除「配置孤儿」）
# ------------------------------------------------------------
# 背景：config.yaml 里曾有 20 余个键**从未被任何代码读取** ——
# 属于"假配置"（改了没效果），且同一份知识在代码里另写死一份常量，
# 两处必然漂移。下列 getter 把这些键收编为唯一事实源：
# 每一组都在**缺 key 时回退到"接线前代码里的硬编码值"**，
# 因此"配置不变 → 行为逐字不变"，接线本身不引入回归。
# ============================================================

def _read_group(mapping, warn=True) -> dict:
    """按 (字段名, 配置键, 默认值) 三元组列表读取一组配置。

    缺 key 时回退默认值并（可选）打印诊断；port 类不做特殊处理，
    数值转换由调用方按需进行（这里统一原样返回，保持 YAML 类型语义）。
    """
    cfg = get_config()
    cfg.load()
    result, missing = {}, []
    for field, key, default in mapping:
        value = cfg.get(key)
        if value is None:
            missing.append(key)
            value = default
        result[field] = value
    if warn and missing:
        _warn_missing(missing)
    return result


def get_session_config() -> dict:
    """会话管理配置（agent/session.py 读取）。

    - ttl_minutes:       会话空闲过期时间（分钟）
    - max_history_turns: 单会话保留的最大历史轮数
    - max_sessions:      会话总数上限（当前仅记录，超限淘汰未实现）
    默认值 = 接线前 agent/session.py 的硬编码值（30 分钟 / 20 轮 / 500）。
    """
    return _read_group((
        ("ttl_minutes", "session.ttl_minutes", 30),
        ("max_history_turns", "session.max_history_turns", 20),
        ("max_sessions", "session.max_sessions", 500),
    ))


def get_history_config() -> dict:
    """多轮对话历史注入配置（agent/session.py / app.py 读取）。

    - turns:            注入 LLM 的最近对话轮数
    - truncate_chars:   单轮答案写入会话历史前的截断字符数
    - llm_answer_chars: 注入 LLM 时对历史"助手"消息的再截断字符数
    默认值 = 接线前的硬编码值（3 轮 / 500 / 300）。
    """
    return _read_group((
        ("turns", "agent.conversation_history_turns", 3),
        ("truncate_chars", "agent.history_truncate_chars", 500),
        ("llm_answer_chars", "agent.history_llm_answer_chars", 300),
    ))


def get_retriever_config() -> dict:
    """检索层参数（domain/retriever/hybrid.py 与 rag_module.py 读取）。

    默认值 = 接线前代码里的硬编码值（RRF k=60、company 加权 2.0、
    候选池倍数 4、单通道候选倍数 3、融合保留 5）。

    2026-09-16 新增二阶段精排键（**消费者：domain/retriever/rerank.py 与
    domain/retriever/hybrid.py**）。默认 `rerank="off"` ⇒ 未开启时行为与接线前
    逐步一致（配置不变 ⇒ 行为不变）。
    """
    return _read_group((
        ("rrf_k", "retriever.rrf_k", 60),
        ("fusion_top_k", "retriever.fusion_top_k", 5),
        ("company_boost", "retriever.company_boost", 2.0),
        ("candidate_multiplier", "retriever.candidate_multiplier", 4),
        ("channel_candidate_multiplier",
         "retriever.channel_candidate_multiplier", 3),
        # ---- 二阶段精排（cross-encoder rerank，2026-09-16 补做）----
        ("rerank", "retriever.rerank", "off"),
        ("rerank_pool_mult", "retriever.rerank_pool_mult", 4),
        ("rerank_alpha", "retriever.rerank_alpha", 0.5),
        ("rerank_model_path", "retriever.rerank_model_path",
         "models/reranker_bge_base"),
        ("rerank_batch_size", "retriever.rerank_batch_size", 8),
        ("rerank_max_length", "retriever.rerank_max_length", 512),
    ))


def get_units_config() -> dict:
    """单位显示配置（domain/units/converter.py 读取）。

    - source_unit:           数据库存储单位（DB 存万元）
    - default_amount_display:金额类默认展示单位
    - display_options:       允许展示的单位列表
    """
    return _read_group((
        ("source_unit", "units.source_unit", "万元"),
        ("default_amount_display", "units.default_amount_display", "亿元"),
        ("display_options", "units.display_options", ["亿元", "万元", "元"]),
    ))


def get_validator_config() -> dict:
    """数值合理性与答案校验阈值（缺 key 回退内置默认）。

    实际消费者只有 domain/units/converter.py 的 validate_sanity（读前 4 个键）。
    后 3 个键（min_answer_length / max_answer_length / forbidden_patterns）随
    2026-09-22 删除 domain/validator/output_guard.py 后已【无消费者】——
    该模块在删除前 get_output_guard() 即零调用方。此处保留仅为不破坏配置形状。
    （原 docstring 写"agent/validator.py 读取"，但该文件从未存在，已更正。）
    """
    return _read_group((
        ("max_ratio_value", "validator.max_ratio_value", 10.0),
        ("max_growth_value", "validator.max_growth_value", 100.0),
        ("max_per_share_value", "validator.max_per_share_value", 100000.0),
        ("min_per_share_value", "validator.min_per_share_value", -1000.0),
        ("min_answer_length", "validator.min_answer_length", 10),
        ("max_answer_length", "validator.max_answer_length", 3000),
        ("forbidden_patterns", "validator.forbidden_patterns",
         ["我不知道", "我猜", "我觉得", "I don't know"]),
    ))


def get_llm_runtime_config() -> dict:
    """LLM 调用侧运行参数（agent/llm.py 读取）。

    - temperature / max_tokens: 请求体采样与长度上限
    - timeout / max_retries:    单次请求超时（秒）与最大重试次数
    默认值 = 接线前 agent/llm.py 的硬编码值（0.1 / 2048 / 30 / 2）。
    """
    return _read_group((
        ("temperature", "llm.temperature", 0.1),
        ("max_tokens", "llm.max_tokens", 2048),
        ("timeout", "llm.timeout", 30),
        ("max_retries", "llm.max_retries", 2),
    ))


def get_coverage_audit_config() -> dict:
    """覆盖审计器配置（agent/engine.py 装配 agent/coverage.py 时读取）。

    - enabled:        是否启用收敛前覆盖审计（默认 True —— 本能力即本期交付物）
    - max_rounds:     单次运行最多审计打回次数（默认 1，防止打回后仍不收敛导致死循环）
    - min_tool_calls: 至少发生过多少次非 dup 工具调用才值得审计（默认 1；0=总是）

    环境变量覆盖：`WB_COVERAGE_AUDIT=0/1`（供评测做"关审计"对照，**不必改 config.yaml**；
    优先级高于 config.yaml，与 .env/真实环境变量的既有优先级一致）。
    """
    conf = _read_group((
        ("enabled", "agent.coverage_audit_enabled", True),
        ("max_rounds", "agent.coverage_audit_max_rounds", 1),
        ("min_tool_calls", "agent.coverage_audit_min_tool_calls", 1),
    ))
    env = os.environ.get("WB_COVERAGE_AUDIT")
    if env is not None and str(env).strip() != "":
        conf["enabled"] = str(env).strip().lower() not in ("0", "false", "no", "off")
    return conf


# ============================================================
# 模块初始化：用 YAML/环境变量覆盖模块级默认常量
# ============================================================
def _init_from_yaml():
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, MAX_STEPS
    global SOFT_ITERATION_LIMIT, CALL_DEDUP, FAILED_CALL_MAX_RETRY, EVALUATOR_ENABLED
    global DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    cfg = get_config()
    cfg.load()

    llm = get_llm_config()
    DEEPSEEK_API_KEY = llm["api_key"]
    DEEPSEEK_BASE_URL = llm["base_url"]
    DEEPSEEK_MODEL = llm["model"]

    db = get_database_config()
    DB_HOST = db["host"]
    DB_PORT = db["port"]
    DB_NAME = db["database"]
    DB_USER = db["user"]
    DB_PASSWORD = db["password"]

    max_iter = cfg.get("agent.max_iterations")
    if max_iter is None:
        _warn_missing(["agent.max_iterations"])
    else:
        MAX_STEPS = int(max_iter)

    # 一期新增：软上限 / 去重开关 / 失败重试上限 / 收敛开关（缺 key 走内置默认）
    soft_limit = cfg.get("agent.soft_iteration_limit")
    if soft_limit is not None:
        SOFT_ITERATION_LIMIT = int(soft_limit)

    dedup = cfg.get("agent.call_dedup")
    if dedup is not None:
        CALL_DEDUP = bool(dedup)

    failed_retry = cfg.get("agent.failed_call_max_retry")
    if failed_retry is not None:
        FAILED_CALL_MAX_RETRY = int(failed_retry)

    evaluator = cfg.get("agent.evaluator_enabled")
    if evaluator is not None:
        EVALUATOR_ENABLED = bool(evaluator)


# 模块导入即初始化：让 get_config() 真正从 YAML 读到配置
_init_from_yaml()
