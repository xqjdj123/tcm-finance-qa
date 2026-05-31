import pandas as pd, os
d = "D:/python-leanrn/codex/data"
out = []
for tbl in ["income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"]:
    df = pd.read_excel(d+"/"+tbl+".xlsx", engine="openpyxl")
    out.append("="*60)
    out.append("【"+tbl+"】 "+str(len(df))+" rows, "+str(len(df.columns))+" cols")
    val_cols = [c for c in df.columns if c not in ("serial_number","stock_code","stock_abbr","report_type","report_period","report_year","created_at")]
    for col in df.columns:
        n = df[col].isnull().sum()
        if n > 0: out.append("  "+col+": "+str(n)+"/"+str(len(df)))
    for c in val_cols:
        vals = df[c].dropna()
        if len(vals)==0: out.append("  "+c+": all null"); continue
        mx, mn, m = vals.max(), vals.min(), vals.mean()
        out.append("  "+c+": ["+str(mn)+","+str(mx)+"] avg="+format(m,".2f")+" non-null="+str(len(vals)))
        if m>0 and mx>m*10:
            bad = vals[vals>m*10]
            out.append("    !!异常大值 "+str(len(bad))+"条")
            for idx in bad.index[:3]:
                r = df.loc[idx]
                out.append("      "+str(r.get("stock_abbr","?"))+" "+str(r.get("report_year","?"))+" = "+str(bad[idx]))
    dupc = ["stock_code","report_year","report_period"]
    if all(c in df.columns for c in dupc):
        dc = df.duplicated(subset=dupc, keep=False).sum()
        if dc > 0: out.append("  !!重复: "+str(dc)+"行")
    out.append("")
print(chr(10).join(out))
open(d+"/quality_report.txt","w",encoding="utf-8").write(chr(10).join(out))
print("报告已保存到 data/quality_report.txt")
