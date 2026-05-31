# -*- coding: utf-8 -*-
"""
追问解析器：统一处理所有追问逻辑
从用户问题中提取意图，自动继承上一轮的槽位
"""
import re


# 公司名列表
COMPANY_NAMES = [
    "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
    "金花股份", "金花", "葵花药业", "达仁堂", "天士力", "康恩贝",
    "东阿阿胶", "江中药业", "健民集团", "千金药业", "羚锐制药",
    "济川药业", "瑞康医药", "昆药集团", "康美药业", "康缘药业",
    "康弘药业", "振东制药", "桂林三金", "太龙药业",
]
COMPANY_ALIAS = {
    "999": "华润三九", "三金": "桂林三金", "白云": "白云山",
    "同仁": "同仁堂", "云白": "云南白药", "花": "葵花药业",
}

# 指标关键词
INDICATOR_KEYWORDS = {
    "净利润": "net_profit", "净利": "net_profit", "归母净利润": "net_profit",
    "营收": "total_operating_revenue", "营业收入": "total_operating_revenue",
    "收入": "total_operating_revenue", "营业总收入": "total_operating_revenue",
    "利润总额": "total_profit", "营业利润": "operating_profit",
    "总资产": "asset_total_assets", "负债": "liability_total_liabilities",
    "净资产": "equity_total_equity",
    "经营现金流": "operating_cf_net_amount", "现金流": "operating_cf_net_amount",
    "毛利率": "gross_profit_margin", "净利率": "net_profit_margin",
    "资产负债率": "asset_liability_ratio",
    "ROE": "roe_weighted_excl_non_recurring",
    "研发费用": "operating_expense_rnd_expenses",
    "销售费用": "operating_expense_selling_expenses",
}

# 中文数字
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


class FollowupType:
    """追问类型"""
    CHART = "chart"           # 图表追问：图片呢、画个图
    REPORT = "report"         # 报告追问：生成报告
    RANK = "rank"             # 排名追问：第二名是谁
    INDICATOR = "indicator"   # 指标追问：那营收呢
    ANALYSIS = "analysis"     # 分析追问：为什么增长
    NORMAL = "normal"         # 普通问题，非追问


class FollowupResolver:
    """追问解析器"""

    # 追问关键词
    CHART_KEYWORDS = ["图片", "画图", "图表", "柱状图", "折线图", "饼图",
                      "可视化", "画一下", "画个图"]
    REPORT_KEYWORDS = ["报告", "报告书", "生成报告", "导出报告"]
    RANK_KEYWORDS = ["第二名", "第三名", "第四名", "第五名", "前三", "前五",
                     "前十", "第一", "第二", "第三", "第四", "第五",
                     "最后一名", "排名"]
    ANALYSIS_KEYWORDS = ["为什么", "原因", "为何", "分析", "解释", "说明",
                         "看法", "归因", "增长", "下降", "变化"]

    def __init__(self):
        pass

    def resolve(self, question, session):
        """
        解析追问，返回：
        {
            "is_followup": bool,
            "type": FollowupType,
            "slots": {"company": ..., "indicator": ..., "year": ..., "period": ...},
            "chart_type": str or None,  # 用户指定的图表类型
            "rank_num": int or None,    # 排名数字
        }
        """
        result = {
            "is_followup": False,
            "type": FollowupType.NORMAL,
            "slots": {},
            "chart_type": None,
            "rank_num": None,
        }

        if not session.history:
            return result

        # 获取上一轮的槽位
        last_slots = session.get_last_slots()

        # 检测追问类型
        followup_type = self._detect_type(question)

        if followup_type == FollowupType.NORMAL:
            # 不是追问，检查是否有部分槽位缺失（可能是简短追问）
            if self._is_likely_followup(question):
                followup_type = FollowupType.INDICATOR
            else:
                return result

        result["is_followup"] = True
        result["type"] = followup_type

        # 从问题中提取新槽位
        new_slots = self._extract_slots(question)

        # 合并槽位：新槽位优先，缺失的从上一轮继承
        merged = dict(last_slots)  # 先复制上一轮
        for k, v in new_slots.items():
            if v is not None:
                merged[k] = v

        result["slots"] = merged

        # 图表追问：提取指定的图表类型
        if followup_type == FollowupType.CHART:
            result["chart_type"] = self._extract_chart_type(question)

        # 排名追问：提取排名数字
        if followup_type == FollowupType.RANK:
            result["rank_num"] = self._extract_rank_number(question)

        return result

    def _detect_type(self, question):
        """检测追问类型"""
        # 优先匹配更具体的类型
        if any(kw in question for kw in self.CHART_KEYWORDS):
            return FollowupType.CHART
        if any(kw in question for kw in self.REPORT_KEYWORDS):
            return FollowupType.REPORT
        if any(kw in question for kw in self.RANK_KEYWORDS):
            return FollowupType.RANK
        if any(kw in question for kw in self.ANALYSIS_KEYWORDS):
            return FollowupType.ANALYSIS
        return FollowupType.NORMAL

    def _is_likely_followup(self, question):
        """判断是否可能是简短追问（如"那营收呢"）"""
        # 包含明确追问词
        followup_words = ["那", "呢", "这个", "那个", "刚才", "上一个", "上面"]
        if any(kw in question for kw in followup_words):
            return True
        # 很短且没有公司名和年份的问题
        if len(question) <= 8:
            has_company = any(name in question for name in COMPANY_NAMES)
            has_year = bool(re.search(r"20\d{2}", question))
            if not has_company and not has_year:
                return True
        return False

    def _extract_slots(self, question):
        """从问题中提取槽位"""
        slots = {}

        # 提取公司名
        for name in COMPANY_NAMES:
            if name in question:
                slots["company"] = COMPANY_ALIAS.get(name, name)
                break

        # 提取指标（先匹配长的关键词）
        for kw in sorted(INDICATOR_KEYWORDS.keys(), key=len, reverse=True):
            if kw in question:
                slots["indicator"] = INDICATOR_KEYWORDS[kw]
                break

        # 提取年份
        m = re.search(r"(20\d{2})年?", question)
        if m:
            slots["year"] = int(m.group(1))

        # 提取报告期
        if "Q1" in question or "一季度" in question:
            slots["period"] = "Q1"
        elif "Q2" in question or "半年" in question or "H1" in question:
            slots["period"] = "H1"
        elif "Q3" in question or "三季度" in question:
            slots["period"] = "Q3"
        elif "年报" in question or "全年" in question or "FY" in question:
            slots["period"] = "FY"

        return slots

    def _extract_chart_type(self, question):
        """从问题中提取图表类型"""
        # 优先匹配更具体的类型
        if any(kw in question for kw in ["横向柱状图", "横向柱状", "横向柱"]):
            return "hbar"
        if any(kw in question for kw in ["折线图", "折线", "趋势图"]):
            return "line"
        if any(kw in question for kw in ["柱状图", "柱状", "条形图"]):
            return "bar"
        if any(kw in question for kw in ["饼图", "饼状图", "环形图"]):
            return "pie"
        return None  # 未指定，使用智能选图

    def _extract_rank_number(self, question):
        """从问题中提取排名数字"""
        # "第二名" → 2
        m = re.search(r"第([一二三四五六七八九十\d]+)", question)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        # "前三" → 3, "前五" → 5
        m = re.search(r"前([一二三四五六七八九十\d]+)", question)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        return None
