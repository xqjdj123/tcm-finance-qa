# -*- coding: utf-8 -*-
"""import_db.py: JSON -> 校验 -> 置信度 -> 入库"""
import sys, os, json, glob, time
sys.path.insert(0, "D:/python-leanrn/codex")
sys.path.insert(0, "D:/python-leanrn/codex/pdf_extractor/lib")
import pymysql
from pdf_extractor.validator import validate_all
from pdf_extractor.confidence_scorer import P as score_p, F as score_f, V as score_v, C as score_c, calc, decide

DB = {"host":"127.0.0.1","port":3306,"user":"root","password":"433127hj","database":"finance_data","charset":"utf8mb4"}
TABLES = {"income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"}
conn = pymysql.connect(**DB); cur = conn.cursor()

TABLE_COLUMNS = {}
for t in TABLES:
    cur.execute("SHOW COLUMNS FROM " + t)
    TABLE_COLUMNS[t] = {r[0] for r in cur.fetchall() if r[0] not in ("serial_number","stock_code","stock_abbr","report_type","report_period","report_year","created_at")}

company_to_code = {}
for t in TABLES:
    try:
        cur.execute("SELECT DISTINCT stock_code, stock_abbr FROM " + t)
        for r in cur.fetchall():
            if r[1]: company_to_code[r[1].replace(" ","")] = r[0]
    except: pass

CODE_TO_NAME = {
    "600085": "同仁堂", "600080": "金花股份", "600129": "太极集团",
    "600222": "太龙药业", "600252": "中恒集团", "600285": "羚锐制药",
    "600329": "达仁堂", "600332": "白云山", "600351": "亚宝药业",
    "600422": "昆药集团", "600436": "片仔癀", "600479": "千金药业",
    "600518": "康美药业", "600535": "天士力", "600557": "康缘药业",
    "600566": "济川药业", "600572": "康恩贝", "600594": "益佰制药",
    "600613": "神奇制药", "600671": "天目药业", "600750": "江中药业",
    "600771": "广誉远", "600976": "健民集团", "600993": "马应龙",
    "603139": "康惠制药", "603439": "贵州三力", "603567": "珍宝岛",
    "603858": "步长制药", "603896": "寿仙谷", "603998": "方盛制药",
    "002082": "万邦德", "000989": "九芝堂", "000999": "华润三九",
    "000538": "云南白药", "000423": "东阿阿胶",
}

jfs = []
for d in ["D:/python-leanrn/codex/data/extracted/sse","D:/python-leanrn/codex/data/extracted/szse"]:
    if os.path.isdir(d): jfs.extend(glob.glob(os.path.join(d,"*.json")))
jfs = [f for f in jfs if "_summary" not in os.path.basename(f)]  # 跳过summary，只导入full
print("共 %d 个JSON待导入" % len(jfs))

for t in TABLES:
    cur.execute("DELETE FROM " + t + " WHERE report_type LIKE 'pdf_%'")
conn.commit()

auto = flagged = rejected = fields = 0
start = time.time()
for idx, fp in enumerate(jfs):
    with open(fp, encoding="utf-8") as f: rec = json.load(f)
    sc, name = str(rec.get("stock_code","")), rec.get("stock_abbr","")
    if name and name.isdigit() and sc in CODE_TO_NAME:
        name = CODE_TO_NAME[sc]
    yr, rp, rtype = rec.get("report_year"), rec.get("report_period"), rec.get("report_type","full")
    tbl_data, source = rec.get("data",{}), rec.get("source","pdfplumber_standard")
    p_score = rec.get("confidence", score_p(source))
    if (not sc or sc == "None") and name:
        sc = company_to_code.get(name.replace(" ",""), "")
    if not sc or not yr or not rp: continue
    rt = "pdf_summary" if rtype == "summary" else "pdf_extracted"
    for tbl in list(tbl_data.keys()):
        if tbl not in TABLE_COLUMNS: del tbl_data[tbl]; continue
        for k in list(tbl_data[tbl].keys()):
            if k not in TABLE_COLUMNS[tbl]: del tbl_data[tbl][k]
        if not tbl_data[tbl]: del tbl_data[tbl]
    if not tbl_data: continue
    p, t, flags = validate_all(tbl_data)
    v_score = score_v(sum(len(v) for v in tbl_data.values()))
    c_score = score_c(p, t)
    conf = calc(p=p_score, f=80, v=v_score, c=c_score)
    decision, _ = decide(conf)
    if decision == "rejected": rejected += 1; continue
    for tbl, kv in tbl_data.items():
        if not kv: continue
        cur.execute("INSERT IGNORE INTO %s (stock_code,stock_abbr,report_year,report_period,report_type) VALUES (%%s,%%s,%%s,%%s,%%s)" % tbl, (sc, name or sc, yr, rp, rt))
        for fld, val in kv.items():
            cur.execute("UPDATE %s SET %s=%%s, report_type=%%s WHERE stock_code=%%s AND report_year=%%s AND report_period=%%s" % (tbl, fld), (val, rt, sc, yr, rp))
        fields += len(kv)
    if decision == "flagged": flagged += 1
    else: auto += 1
    conn.commit()
    if (idx+1) % 100 == 0:
        print("  [%d/%d] auto=%d flag=%d reject=%d" % (idx+1, len(jfs), auto, flagged, rejected))
print("\n完成! %ds" % (time.time()-start))
print("自动: %d  待复核: %d  不入库: %d  字段: %d" % (auto, flagged, rejected, fields))
for t in TABLES:
    cur.execute("SELECT COUNT(*) FROM " + t + " WHERE report_type LIKE 'pdf_%'")
    print("  [%s] %d行" % (t, cur.fetchone()[0]))
cur.close(); conn.close()
