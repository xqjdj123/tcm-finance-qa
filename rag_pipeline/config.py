# -*- coding: utf-8 -*-
"""
RAG Pipeline 配置
路径、数据库、Ollama 等全局常量
"""
import os
import sys


def _ensure_repo_root_on_path():
    """把仓库根加入 sys.path，使 `import domain.*` / `import agent.*` 可用。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "domain", "normalize")):
            if d not in sys.path:
                sys.path.insert(0, d)
            return d
        d = os.path.dirname(d)
    return ""


_REPO_ROOT = _ensure_repo_root_on_path()


def _load_db_config():
    """数据库连接参数：统一委托 `agent.config.get_database_config()`
    （YAML + 环境变量覆盖的唯一入口）。

    原实现把 host/port/database/user **四项写死在代码里**，只把 password 从
    config.yaml 手工读出来 —— 与 agent/config.py 并存的"第二套 config 读取逻辑"。
    2026-09 收敛；except 分支仅作 agent.config 不可用时的安全兜底。
    """
    try:
        from agent.config import get_database_config
        cfg = get_database_config()
        if cfg:
            return dict(cfg)
    except Exception as e:
        print(f"[rag_pipeline.config] 读取 agent.config 失败，回退环境变量: {e}")
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "database": os.environ.get("DB_NAME", "finance_data"),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASSWORD", ""),
    }


# ===== 路径配置 =====
# 由 __file__ 推导仓库根，不写死绝对路径
BASE_DIR = _REPO_ROOT or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD_DIR = os.path.join(BASE_DIR, 'data', '研报总MD')
RAG_INDEX_DIR = os.path.join(BASE_DIR, 'models', 'rag_index')

# ===== FAISS/BM25 索引文件 =====
FAISS_INDEX_PATH = os.path.join(RAG_INDEX_DIR, 'faiss.index')
BM25_PATH = os.path.join(RAG_INDEX_DIR, 'bm25.pkl')
INDEX_META_PATH = os.path.join(RAG_INDEX_DIR, 'index_meta.json')

# ===== 中间产物 =====
CHUNKS_JSON_PATH = os.path.join(BASE_DIR, 'data', 'chunks.json')

# ===== MySQL 配置 =====
# 统一委托 agent.config（不再内联 host/port/database/user 字面量）
DB_CONFIG = _load_db_config()
TABLE_NAME = 'rag_chunks'

# ===== Ollama 配置 =====
OLLAMA_BASE_URL = 'http://localhost:11434'
EMBED_MODEL = 'bge-m3'
EMBED_DIM = 1024
RERANK_MODEL = 'qwen3.5:9b'

# ===== 分块参数 =====
MIN_TOKENS = 100
MAX_TOKENS = 512
MIN_CHUNK_TOKENS = 50

# ===== 检索参数 =====
VECTOR_TOP_K = 20
BM25_TOP_K = 20
RRF_FUSE_TOP_K = 20
RERANK_TOP_K = 3

# ===== 页脚截断关键词 =====
FOOTER_KEYWORDS_BASE = [
    '免责声明', '评级说明', '分析师声明',
    '重要声明', '风险提示声明',
    '不构成投资建议', '版权属于', '未经书面授权',
]

# ===== 公司名字典 =====
# 唯一真相源 domain/normalize/company_names.py（原为 46 家内联副本，
# 2026-09-14 收敛；SSOT 是其超集，且已收录本处曾有的全部别名）。
try:
    from domain.normalize.company_names import (
        all_names as _ssot_all_names, all_aliases as _ssot_all_aliases,
    )
    COMPANY_NAMES = _ssot_all_names()
    # 索引期是"在研报正文里做子串扫描"，单字别名（'花'）会误命中
    # 「花费」等日常词，故剔除——与 SSOT 的 _MIN_SCAN_LEN 规则一致。
    COMPANY_ALIAS = {a: s for a, s in _ssot_all_aliases().items() if len(a) >= 2}
except Exception as e:  # 兜底：SSOT 不可用时保持服务可用（会打印诊断）
    print(f"[rag_pipeline.config] 加载公司名 SSOT 失败，公司识别降级为空表: {e}")
    COMPANY_NAMES = []
    COMPANY_ALIAS = {}
REPORT_TYPE_PATTERNS = [
    (r'年报.*点评|年报.*业绩', '年报点评'),
    (r'半年报.*点评|半年报.*业绩|H1|半年', '半年报点评'),
    (r'三季报.*点评|三季报.*业绩|Q3', '三季报点评'),
    (r'一季报.*点评|一季报.*业绩|Q1', '一季报点评'),
    (r'首次覆盖', '首次覆盖'),
    (r'行业.*研究|行业.*概览|行业.*白皮书|行业.*策略', '行业研究'),
    (r'业绩预告|业绩快报', '业绩预告'),
    (r'公司.*点评|点评报告', '公司点评'),
]
