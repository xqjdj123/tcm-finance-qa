# -*- coding: utf-8 -*-
"""session.py: 对话会话管理
管理多轮对话的槽位继承和历史记录。
每个session_id对应一个独立的对话上下文。
"""
import time


class SessionManager:
    def __init__(self, max_history=20, timeout=1800):
        """
        Args:
            max_history: 每个session保留的最大历史轮数
            timeout: session超时时间（秒），默认30分钟
        """
        self.sessions = {}  # {session_id: Session}
        self.max_history = max_history
        self.timeout = timeout

    def get_or_create(self, session_id):
        """获取或创建session"""
        self._cleanup()
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id, self.max_history)
        session = self.sessions[session_id]
        session.last_active = time.time()
        return session

    def get(self, session_id):
        """获取session，不存在返回None"""
        self._cleanup()
        return self.sessions.get(session_id)

    def reset(self, session_id):
        """重置session"""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def _cleanup(self):
        """清理过期session"""
        now = time.time()
        expired = [sid for sid, s in self.sessions.items() if now - s.last_active > self.timeout]
        for sid in expired:
            del self.sessions[sid]


class Session:
    def __init__(self, session_id, max_history=20):
        self.session_id = session_id
        self.last_active = time.time()
        self.history = []  # [(question, answer, slots)]
        self.max_history = max_history
        self.slots = {
            "company": None,
            "indicator": None,
            "year": None,
            "period": None,
            "intent": None,
        }

    def update_slots(self, new_slots):
        """更新槽位，只覆盖非None的值"""
        for k, v in new_slots.items():
            if v is not None and v != "":
                self.slots[k] = v

    def get_context(self):
        """获取当前上下文（用于多轮对话）"""
        return {k: v for k, v in self.slots.items() if v is not None}

    def add_turn(self, question, answer, slots):
        """记录一轮对话"""
        self.history.append({
            "question": question,
            "answer": answer,
            "slots": dict(slots),
            "time": time.time(),
        })
        # 限制历史长度
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def get_recent_history(self, n=3):
        """获取最近n轮历史"""
        return self.history[-n:]

    def is_followup(self, question):
        """判断是否是追问（短问题、没有公司名、没有指标关键词）

        这是一个简单的启发式判断，不依赖模型。
        """
        # 太短的问题很可能是追问
        if len(question) < 8:
            return True

        # 检查是否包含公司名或指标关键词
        indicator_keywords = ["净利润", "营收", "收入", "利润", "资产", "负债",
                              "现金流", "每股", "ROE", "roe", "毛利", "净利",
                              "同比", "环比", "研发", "销售费用"]
        has_indicator = any(kw in question for kw in indicator_keywords)

        # 检查是否包含年份
        import re
        has_year = bool(re.search(r"20\d{2}", question))

        # 没有指标且没有年份 → 很可能是追问
        if not has_indicator and not has_year:
            return True

        return False

    def merge_question(self, question):
        """将追问与上下文合并，补全缺失信息

        例如：
        - 上下文: company="白云山", indicator="净利润", year=2023
        - 追问: "那2025年呢"
        - 合并后: company="白云山", indicator="净利润", year=2025
        """
        import re
        from time_parser import parse_time

        # 从追问中提取信息
        time_info = parse_time(question)
        new_year = time_info.get("year")
        new_period = time_info.get("period")

        # 从追问中提取公司名（简单字典匹配，不依赖pipeline）
        _COMPANY_NAMES = ["白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "步长制药",
            "片仔癀", "济川药业", "天士力", "达仁堂", "瑞康医药", "昆药集团", "康恩贝",
            "葵花药业", "康美药业", "康缘药业", "东阿阿胶", "江中药业", "健民集团",
            "康弘药业", "千金药业", "振东制药", "羚锐制药", "金花股份", "桂林三金",
            "太龙药业", "999", "白药", "金花", "花"]
        _COMPANY_ALIAS = {"999": "华润三九", "三金": "桂林三金", "白云": "白云山",
            "同仁": "同仁堂", "云白": "云南白药", "花": "葵花药业"}
        new_company = None
        for n in _COMPANY_NAMES:
            if n in question:
                new_company = _COMPANY_ALIAS.get(n, n)
                break

        # 从追问中提取指标
        indicator_keywords = {
            "净利润": "net_profit", "营收": "total_operating_revenue",
            "收入": "total_operating_revenue", "利润总额": "total_profit",
            "营业利润": "operating_profit", "毛利率": "gross_profit_margin",
            "净利率": "net_profit_margin", "ROE": "roe",
            "资产负债率": "asset_liability_ratio", "研发费用": "operating_expense_rnd_expenses",
            "销售费用": "operating_expense_selling_expenses",
        }
        new_indicator = None
        for kw, col in indicator_keywords.items():
            if kw in question:
                new_indicator = col
                break

        # 合并：追问的值覆盖上下文，没有的继承上下文
        merged = dict(self.slots)
        if new_company:
            merged["company"] = new_company
        if new_indicator:
            merged["indicator"] = new_indicator
        if new_year:
            merged["year"] = new_year
        if new_period:
            merged["period"] = new_period

        return merged


# 全局session管理器
_manager = None

def get_session_manager():
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager


if __name__ == "__main__":
    # 测试
    mgr = SessionManager()

    # 第1轮
    s = mgr.get_or_create("test_user")
    s.update_slots({"company": "白云山", "indicator": "net_profit", "year": 2023})
    s.add_turn("白云山2023年净利润", "40.56亿元", s.slots)
    print("第1轮:", s.get_context())

    # 第2轮：追问
    merged = s.merge_question("那2025年呢")
    print("第2轮合并:", merged)

    # 判断是否追问
    print("是追问?", s.is_followup("那2025年呢"))
    print("是追问?", s.is_followup("云南白药2023年净利润"))
