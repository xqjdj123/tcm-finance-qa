import pandas as pd
import mysql.connector
import os

def _load_db_config():
    """数据库连接参数：统一委托 agent.config.get_database_config()（唯一配置入口）。

    原实现是"第二套 config.yaml 读取逻辑"（只读 password，
    host/port/user/database 硬编码在下方 connect 调用里）—— 2026-09 收敛。
    """
    import os as _os, sys as _sys
    d = _os.path.dirname(_os.path.abspath(__file__))
    for _ in range(4):
        if _os.path.isfile(_os.path.join(d, "agent", "config.py")):
            if d not in _sys.path:
                _sys.path.insert(0, d)
            break
        d = _os.path.dirname(d)
    try:
        from agent.config import get_database_config
        cfg = get_database_config()
        if cfg:
            return dict(cfg)
    except Exception as e:
        print(f"[db] 读取 agent.config 失败，回退环境变量: {e}")
    return {
        "host": _os.environ.get("DB_HOST", "localhost"),
        "port": int(_os.environ.get("DB_PORT", "3306")),
        "user": _os.environ.get("DB_USER", "root"),
        "password": _os.environ.get("DB_PASSWORD", ""),
        "database": _os.environ.get("DB_NAME", "finance_data"),
    }


conn = mysql.connector.connect(**_load_db_config())

tables = [
    "income_sheet",
    "balance_sheet",
    "cash_flow_sheet",
    "core_performance_indicators_sheet"
]

outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(outdir, exist_ok=True)

for tbl in tables:
    sql = "SELECT * FROM " + tbl + " WHERE report_type LIKE \"pdf%\""
    df = pd.read_sql(sql, conn)
    path = os.path.join(outdir, tbl + ".xlsx")
    df.to_excel(path, index=False)
    print(f"{tbl}: {len(df)} rows -> {path}")

conn.close()
print("\nDone!")
