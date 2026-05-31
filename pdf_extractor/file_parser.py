# -*- coding: utf-8 -*-
"""
file_parser.py: PDF文件名解析 + 内容元信息提取
上交所: 600085_20250404_9F44.pdf → code=600085, year=2025, period=Q1
深交所: 云南白药：2024年年度报告.pdf → name=云南白药, year=2024, period=FY
M0: 用pdf_segmenter分块后提取股票代码（不依赖外部映射、不硬编码范围）
"""
import os, re, json
import pdfplumber

DATE_TO_PERIOD = {1:"FY",2:"FY",3:"FY",4:"Q1",5:"Q1",6:"H1",7:"H1",8:"H1",9:"H1",10:"Q3",11:"Q3",12:"FY"}

# 股票代码提取模式（支持冒号和空格两种格式）
_CODE_PATS = [
    re.compile(r'股票代码[：:]\s*(\d{6})'),
    re.compile(r'股票代码\s+(\d{6})'),
    re.compile(r'证券代码[：:]\s*(\d{6})'),
    re.compile(r'证券代码\s+(\d{6})'),
    re.compile(r'A股代码[：:]\s*(\d{6})'),
]


def extract_code_from_pdf(fp, max_pages=15):
    """从PDF内容中提取股票代码（逐页扫描，找到即停）"""
    try:
        with pdfplumber.open(fp) as pdf:
            for i in range(min(max_pages, len(pdf.pages))):
                text = pdf.pages[i].extract_text() or ""
                for pat in _CODE_PATS:
                    m = pat.search(text)
                    if m:
                        return m.group(1)
    except:
        pass
    return None
CN_PERIOD = sorted([
    ("一季度报告","Q1"),("第一季度","Q1"),("一季报","Q1"),
    ("半年度报告","H1"),("半年度","H1"),("半年报","H1"),("中期报告","H1"),
    ("三季度报告","Q3"),("第三季度","Q3"),("三季报","Q3"),
    ("年度报告","FY"),("年报","FY"),("全年","FY"),
], key=lambda x: -len(x[0]))

def parse_sse(fn):
    name = os.path.splitext(fn)[0]
    parts = name.split("_")
    if len(parts) < 2: return None, None, None
    sc = parts[0]; ds = parts[1]
    if not ds.isdigit() or len(ds) < 8: return sc, None, None
    y = int(ds[:4]); m = int(ds[4:6])
    rp = DATE_TO_PERIOD.get(m, "FY")
    ry = y-1 if (rp=="FY" and m<=4) else y
    return sc, ry, rp

def parse_szse(fn):
    name = os.path.splitext(fn)[0]
    m = re.match(r"(.+?)[：:]\s*(\d{4})年(.+)", name)
    if m:
        c = m.group(1).strip(); y = int(m.group(2)); ps = m.group(3)
        for kw,code in CN_PERIOD:
            if kw in ps: return c, y, code
        return c, y, "FY"
    m2 = re.search(r"(\d{4})年", name)
    if m2:
        y = int(m2.group(1))
        for kw,code in CN_PERIOD:
            if kw in name: return name.split(kw)[0].strip()[:20], y, code
    return None, None, None

def classify_pdf(fp):
    sz = os.path.getsize(fp)
    fn = os.path.basename(fp)
    if "摘要" in fn or sz < 200*1024: return "summary"
    return "full"

def get_all_pdfs(data_dir):
    results = []
    for root, dirs, files in os.walk(data_dir):
        for f in files:
            if not f.endswith(".pdf"): continue
            fp = os.path.join(root, f)
            if "上交所" in root:
                code, yr, rp = parse_sse(f)
                results.append({"fp":fp,"fn":f,"ex":"SSE","code":code,"name":None,"yr":yr,"rp":rp,"sz":os.path.getsize(fp),"type":classify_pdf(fp)})
            elif "深交所" in root:
                nm, yr, rp = parse_szse(f)
                sc = extract_code_from_pdf(fp)
                results.append({"fp":fp,"fn":f,"ex":"SZSE","code":sc,"name":nm,"yr":yr,"rp":rp,"sz":os.path.getsize(fp),"type":classify_pdf(fp)})
    return results
