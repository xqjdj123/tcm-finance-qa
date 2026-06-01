# -*- coding: utf-8 -*-
"""
import_db.py: JSON -> 校验 -> 置信度 -> 单位统一 -> 入库
集成模块：数据导入、单位识别、异常检测、pending_review
"""
import sys, os, json, glob, time
sys.path.insert(0, "D:/python-leanrn/codex")
sys.path.insert(0, "D:/python-leanrn/codex/pdf_extractor/lib")
import pymysql
from pdf_extractor.validator import validate_all
from pdf_extractor.confidence_scorer import P as score_p, F as score_f, V as score_v, C as score_c, calc, decide

# ===== 数据库配置 =====
DB = {"host":"127.0.0.1","port":3306,"user":"root","password":"433127hj","database":"finance_data","charset":"utf8mb4"}
TABLES = {"income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"}

# ===== 字段中文名映射 =====
FIELD_CN = {
    "total_operating_revenue": "营业总收入",
    "net_profit": "净利润",
    "total_profit": "利润总额",
    "operating_profit": "营业利润",
    "asset_total_assets": "总资产",
    "liability_total_liabilities": "总负债",
    "equity_total_equity": "净资产",
    "operating_cf_net_amount": "经营现金流净额",
    "investing_cf_net_amount": "投资现金流净额",
    "financing_cf_net_amount": "筹资现金流净额",
}

# ===== 单位倍率 =====
UNIT_FACTOR = {
    "元": 1,
    "千元": 1000,
    "万元": 10000,
    "亿元": 100000000,
}

# ===== 需要检查的字段（用于异常检测） =====
CHECK_FIELDS = {
    "income_sheet": ["total_operating_revenue", "net_profit"],
    "balance_sheet": ["asset_total_assets"],
    "cash_flow_sheet": ["operating_cf_net_amount"],
}


class DataImporter:
    """数据导入器：集成单位识别、异常检测、pending_review"""

    def __init__(self):
        self.conn = pymysql.connect(**DB)
        self.cur = self.conn.cursor()
        self.table_columns = {}
        self.company_to_code = {}
        self._init_db()

    def _init_db(self):
        """初始化数据库连接和表结构"""
        # 获取表结构
        for t in TABLES:
            self.cur.execute("SHOW COLUMNS FROM " + t)
            self.table_columns[t] = {r[0] for r in self.cur.fetchall()
                                     if r[0] not in ("serial_number","stock_code","stock_abbr",
                                                     "report_type","report_period","report_year","created_at")}

        # 获取公司代码映射
        for t in TABLES:
            try:
                self.cur.execute("SELECT DISTINCT stock_code, stock_abbr FROM " + t)
                for r in self.cur.fetchall():
                    if r[1]: self.company_to_code[r[1].replace(" ","")] = r[0]
            except: pass

        # 确保 pending_review 表存在
        self._ensure_pending_review_table()

        # 确保 report_metadata 表存在
        self._ensure_metadata_table()

    def _ensure_pending_review_table(self):
        """确保 pending_review 表存在"""
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_review (
                id INT PRIMARY KEY AUTO_INCREMENT,
                company VARCHAR(100),
                stock_code VARCHAR(10),
                period VARCHAR(20),
                indicator VARCHAR(100),
                table_name VARCHAR(50),
                raw_value DECIMAL(20,4),
                unit_raw VARCHAR(20),
                reason VARCHAR(200),
                source_file VARCHAR(255),
                status VARCHAR(20) DEFAULT 'pending',
                reviewed_at DATETIME,
                review_comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def _ensure_metadata_table(self):
        """确保 report_metadata 表存在"""
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS report_metadata (
                id INT AUTO_INCREMENT PRIMARY KEY,
                stock_code VARCHAR(10),
                stock_abbr VARCHAR(50),
                report_year INT,
                report_period VARCHAR(10),
                source_file VARCHAR(200),
                report_unit VARCHAR(50),
                unit_factor DECIMAL(20,4),
                unit_source_text VARCHAR(500),
                extract_confidence DECIMAL(5,2),
                review_status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_report (stock_code, report_year, report_period)
            )
        """)
        self.conn.commit()

    def import_all(self):
        """导入所有JSON文件"""
        jfs = []
        for d in ["D:/python-leanrn/codex/data/extracted/sse","D:/python-leanrn/codex/data/extracted/szse"]:
            if os.path.isdir(d): jfs.extend(glob.glob(os.path.join(d,"*.json")))
        jfs = [f for f in jfs if "_summary" not in os.path.basename(f)]

        print("共 %d 个JSON待导入" % len(jfs))

        # 清空旧数据
        for t in TABLES:
            self.cur.execute("DELETE FROM " + t + " WHERE report_type LIKE 'pdf_%'")
        self.conn.commit()

        auto = flagged = rejected = fields = 0
        start = time.time()

        for idx, fp in enumerate(jfs):
            result = self._import_one(fp)
            if result == "auto": auto += 1
            elif result == "flagged": flagged += 1
            elif result == "rejected": rejected += 1
            fields += 1

            if (idx+1) % 100 == 0:
                print("  [%d/%d] auto=%d flag=%d reject=%d" % (idx+1, len(jfs), auto, flagged, rejected))

        print("\n完成! %ds" % (time.time()-start))
        print("自动: %d  待复核: %d  不入库: %d  字段: %d" % (auto, flagged, rejected, fields))

        for t in TABLES:
            self.cur.execute("SELECT COUNT(*) FROM " + t + " WHERE report_type LIKE 'pdf_%'")
            print("  [%s] %d行" % (t, self.cur.fetchone()[0]))

    def _import_one(self, fp):
        """导入单个JSON文件"""
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

        with open(fp, encoding="utf-8") as f:
            rec = json.load(f)

        sc = str(rec.get("stock_code",""))
        name = rec.get("stock_abbr","")
        if name and name.isdigit() and sc in CODE_TO_NAME:
            name = CODE_TO_NAME[sc]
        yr = rec.get("report_year")
        rp = rec.get("report_period")
        rtype = rec.get("report_type","full")
        tbl_data = rec.get("data",{})
        source = rec.get("source","pdfplumber_standard")
        unit_info = rec.get("unit_info", {})
        p_score = rec.get("confidence", score_p(source))

        if (not sc or sc == "None") and name:
            sc = self.company_to_code.get(name.replace(" ",""), "")
        if not sc or not yr or not rp:
            return "rejected"

        rt = "pdf_summary" if rtype == "summary" else "pdf_extracted"

        # 过滤字段
        for tbl in list(tbl_data.keys()):
            if tbl not in self.table_columns: del tbl_data[tbl]; continue
            for k in list(tbl_data[tbl].keys()):
                if k not in self.table_columns[tbl]: del tbl_data[tbl][k]
            if not tbl_data[tbl]: del tbl_data[tbl]

        if not tbl_data:
            return "rejected"

        # 校验
        p, t, flags = validate_all(tbl_data)
        v_score = score_v(sum(len(v) for v in tbl_data.values()))
        c_score = score_c(p, t)
        conf = calc(p=p_score, f=80, v=v_score, c=c_score)
        decision, _ = decide(conf)

        if decision == "rejected":
            return "rejected"

        # 获取单位信息
        report_unit, unit_factor, unit_source_text = self._extract_unit(unit_info, tbl_data)

        # 保存 metadata
        self._save_metadata(sc, name, yr, rp, fp, report_unit, unit_factor, unit_source_text, conf)

        # 入库
        self._save_data(sc, name, yr, rp, rt, tbl_data, report_unit)

        # 异常检测：插入 pending_review
        self._detect_anomaly(sc, name, yr, rp, tbl_data, report_unit, fp)

        if decision == "flagged":
            return "flagged"
        return "auto"

    def _extract_unit(self, unit_info, tbl_data):
        """从 unit_info 提取单位信息"""
        report_unit = "万元"
        unit_factor = 10000
        unit_source_text = ""

        for tt in tbl_data.keys():
            if tt in unit_info:
                report_unit = unit_info[tt].get("unit_raw", "万元")
                unit_factor = unit_info[tt].get("unit_factor", 10000)
                unit_source_text = unit_info[tt].get("unit_source_text", "")
                break

        return report_unit, unit_factor, unit_source_text

    def _save_metadata(self, sc, name, yr, rp, fp, report_unit, unit_factor, unit_source_text, conf):
        """保存 report_metadata"""
        try:
            self.cur.execute("""
                INSERT INTO report_metadata (stock_code, stock_abbr, report_year, report_period,
                    source_file, report_unit, unit_factor, unit_source_text, extract_confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    source_file=VALUES(source_file), report_unit=VALUES(report_unit),
                    unit_factor=VALUES(unit_factor), unit_source_text=VALUES(unit_source_text),
                    extract_confidence=VALUES(extract_confidence)
            """, (sc, name or sc, yr, rp, fp, report_unit, unit_factor, unit_source_text, conf))
        except Exception as e:
            print("  [WARN] metadata save failed: %s" % e)

    def _save_data(self, sc, name, yr, rp, rt, tbl_data, report_unit):
        """保存数据到各表"""
        for tbl, kv in tbl_data.items():
            if not kv: continue
            self.cur.execute("""
                INSERT IGNORE INTO %s (stock_code,stock_abbr,report_year,report_period,report_type,unit_raw,unit_std)
                VALUES (%%s,%%s,%%s,%%s,%%s,%%s,%%s)
            """ % tbl, (sc, name or sc, yr, rp, rt, report_unit, "万元"))

            for fld, val in kv.items():
                self.cur.execute("""
                    UPDATE %s SET %s=%%s, report_type=%%s, unit_raw=%%s, unit_std=%%s
                    WHERE stock_code=%%s AND report_year=%%s AND report_period=%%s
                """ % (tbl, fld), (val, rt, report_unit, "万元", sc, yr, rp))

        self.conn.commit()

    def _detect_anomaly(self, sc, name, yr, rp, tbl_data, report_unit, fp):
        """检测异常数据，插入 pending_review"""
        for tbl, fields in CHECK_FIELDS.items():
            if tbl not in tbl_data:
                continue

            for field in fields:
                if field not in tbl_data[tbl]:
                    continue

                val = tbl_data[tbl][field]
                if val is None:
                    continue

                val = float(val)

                # 检查是否异常大（> 1亿）
                if abs(val) > 1e8:
                    # 检查是否是元单位（需要转换）
                    if report_unit == "元":
                        # 插入 pending_review
                        indicator_cn = FIELD_CN.get(field, field)
                        try:
                            self.cur.execute("""
                                INSERT INTO pending_review
                                (company, stock_code, period, indicator, table_name, raw_value, unit_raw, reason, source_file)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (name, sc, rp, indicator_cn, tbl, val, report_unit,
                                  "数据异常大，可能单位错误", fp))
                        except Exception as e:
                            pass

        self.conn.commit()

    def review_pending(self):
        """审核 pending_review 表中的记录"""
        self.cur.execute("SELECT COUNT(*) FROM pending_review WHERE status = 'pending'")
        count = self.cur.fetchone()[0]

        if count == 0:
            print("没有待审核记录")
            return

        print("\n待审核记录: %d 条" % count)
        print("=" * 50)

        self.cur.execute("SELECT * FROM pending_review WHERE status = 'pending' ORDER BY id")
        records = self.cur.fetchall()

        for record in records:
            print("\nID: %s" % record[0])
            print("公司: %s" % record[1])
            print("期间: %s" % record[3])
            print("指标: %s" % record[4])
            print("原始值: %s" % record[6])
            print("当前单位: %s" % record[7])
            print("原因: %s" % record[8])
            print("来源: %s" % record[9])
            print("-" * 50)

            print("请选择单位:")
            print("1. 元")
            print("2. 万元")
            print("3. 亿元")
            print("s. 跳过")
            print("q. 退出")

            choice = input("选择: ").strip()

            if choice == 'q':
                break
            elif choice == 's':
                continue
            elif choice in ('1', '2', '3'):
                unit_name, unit_factor = {
                    '1': ('元', 1),
                    '2': ('万元', 10000),
                    '3': ('亿元', 100000000),
                }[choice]
                self._apply_review(record, unit_name, unit_factor)
                print("已审核")
            else:
                print("无效选择")

    def _apply_review(self, record, unit_name, unit_factor):
        """应用审核结果"""
        record_id = record[0]
        company = record[1]
        stock_code = record[2]
        period = record[3]
        indicator_cn = record[4]
        table_name = record[5]
        raw_value = float(record[6])

        # 把中文指标名转换成英文字段名
        indicator_en = None
        for en, cn in FIELD_CN.items():
            if cn == indicator_cn:
                indicator_en = en
                break

        if not indicator_en:
            print("  无法识别指标: %s" % indicator_cn)
            return

        # 计算标准值（万元）
        std_value = raw_value * unit_factor / 10000

        # 更新 pending_review 状态
        self.cur.execute("""
            UPDATE pending_review
            SET status = 'approved', unit_raw = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (unit_name, record_id))

        # 更新实际表
        try:
            self.cur.execute("""
                UPDATE %s SET %s = %%s, unit_raw = '万元', unit_std = '万元'
                WHERE stock_code = %%s AND report_period = %%s
            """ % (table_name, indicator_en), (std_value, stock_code, period))
            print("  已更新 %s.%s = %.2f 万元" % (table_name, indicator_cn, std_value))
        except Exception as e:
            print("  更新失败: %s" % e)

        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        self.cur.close()
        self.conn.close()


def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description='数据导入工具')
    parser.add_argument('--import', action='store_true', help='导入所有JSON文件')
    parser.add_argument('--review', action='store_true', help='审核 pending_review 表')
    args = parser.parse_args()

    importer = DataImporter()

    try:
        if args.import_:
            importer.import_all()
        elif args.review:
            importer.review_pending()
        else:
            # 默认执行导入
            importer.import_all()
    finally:
        importer.close()


if __name__ == "__main__":
    main()
