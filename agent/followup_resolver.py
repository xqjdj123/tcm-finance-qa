# -*- coding: utf-8 -*-
"""
追问解析器：检测追问类型，合并理解结果
不再自行提取 slot（由 pipeline.understand() 统一完成）
"""
import re

# 公司名列表示例（仅用于"那营收呢"这类简短追问的检测）
COMPANY_NAMES = [
    "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
    "金花股份", "金花", "葵花药业", "达仁堂", "天士力", "康恩贝",
    "东阿阿胶", "江中药业", "健民集团", "千金药业", "羚锐制药",
    "济川药业", "瑞康医药", "昆药集团", "康美药业", "康缘药业",
    "康弘药业", "振东制药", "桂林三金", "太龙药业",
]


class FollowupType:
    CHART = "chart"
    REPORT = "report"
    RANK = "rank"
    INDICATOR = "indicator"
    ANALYSIS = "analysis"
    NORMAL = "normal"


class FollowupResolver:
    """追问解析器"""

    CHART_KEYWORDS = ["图片", "画图", "图表", "柱状图", "折线图", "饼图", "可视化", "画一个", "画个图"]
    REPORT_KEYWORDS = ["报告", "报告书", "生成报告", "导出报告"]
    RANK_KEYWORDS = ["第二名", "第三名", "第四名", "第五名", "前三", "前五",
                     "前十", "第一", "第二", "第三", "第四", "第五",
                     "最后一名", "排名"]
    ANALYSIS_KEYWORDS = ["为什么", "原因", "为何", "分析", "解释", "说明",
                         "看法", "归因", "增长", "下降", "变化"]

    FOLLOWUP_WORDS = ["那", "呢", "这个", "那个", "刚才", "上一个", "上面"]

    def resolve(self, question, last_understanding):
        """
        解析追问，结合上次理解结果返回新的理解
        参数：
          question: str - 当前问题
          last_understanding: dict - 上次 pipeline.understand() 的结果
        返回：
          {
            "is_followup": bool,
            "type": FollowupType,
            "understanding": dict - 合并后的理解结果
            "chart_type": str or None,
            "rank_num": int or None,
          }
        """
        result = {
            "is_followup": False,
            "type": FollowupType.NORMAL,
            "understanding": dict(last_understanding) if last_understanding else {},
            "chart_type": None,
            "rank_num": None,
        }

        if not last_understanding:
            return result

        # 检测追问类型
        ftype = self._detect_type(question)

        if ftype == FollowupType.NORMAL:
            # 是否属于简短追问（如"那营收呢"）
            if self._is_likely_followup(question):
                ftype = FollowupType.INDICATOR
            else:
                return result

        result["is_followup"] = True
        result["type"] = ftype

        # 合并理解：新问题中提取到的新信息覆盖旧信息
        # （这里无法调用 pipeline.understand，由 Agent 在上层完成合并）
        # 追问类型特定处理
        if ftype == FollowupType.CHART:
            result["chart_type"] = self._extract_chart_type(question)
        if ftype == FollowupType.RANK:
            result["rank_num"] = self._extract_rank_number(question)

        return result

    def _detect_type(self, question):
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
        if any(kw in question for kw in self.FOLLOWUP_WORDS):
            return True
        if len(question) <= 8:
            has_company = any(name in question for name in COMPANY_NAMES)
            has_year = bool(re.search(r"20\d{2}", question))
            if not has_company and not has_year:
                return True
        return False

    def _extract_chart_type(self, question):
        if any(kw in question for kw in ["横向柱状图", "横向柱状", "横向柱"]):
            return "hbar"
        if any(kw in question for kw in ["折线图", "折线", "趋势图"]):
            return "line"
        if any(kw in question for kw in ["柱状图", "柱状", "条形图"]):
            return "bar"
        if any(kw in question for kw in ["饼图", "饼状图", "环形图"]):
            return "pie"
        return None

    @staticmethod
    def _extract_rank_number(question):
        """从问题中提取排名数字"""
        CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        m = re.search(r"第([一二三四五六七八九十\d]+)", question)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        m = re.search(r"前([一二三四五六七八九十\d]+)", question)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        return None
