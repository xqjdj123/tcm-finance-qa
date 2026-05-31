import pandas as pd
import mysql.connector
import os

conn = mysql.connector.connect(
    host="localhost", port=3306,
    user="root", password="433127hj",
    database="finance_data"
)

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
