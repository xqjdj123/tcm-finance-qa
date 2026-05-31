# -*- coding: utf-8 -*-
"""会话状态管理（基础设施，不是工具）"""
import time
import re


class SessionManager:
    def __init__(self, max_history=20, timeout=1800):
        self.sessions = {}
        self.max_history = max_history
        self.timeout = timeout

    def get_or_create(self, session_id):
        self._cleanup()
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id, self.max_history)
        s = self.sessions[session_id]
        s.last_active = time.time()
        return s

    def get(self, session_id):
        self._cleanup()
        return self.sessions.get(session_id)

    def reset(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def _cleanup(self):
        now = time.time()
        expired = [sid for sid, s in self.sessions.items() if now - s.last_active > self.timeout]
        for sid in expired:
            del self.sessions[sid]


class Session:
    def __init__(self, session_id, max_history=20):
        self.session_id = session_id
        self.last_active = time.time()
        self.history = []
        self.max_history = max_history
        self.slots = {
            "company": None,
            "indicator": None,
            "year": None,
            "period": None,
        }

    def update_slots(self, new_slots):
        for k, v in new_slots.items():
            if v is not None and v != "":
                self.slots[k] = v

    def get_context(self):
        return {k: v for k, v in self.slots.items() if v is not None}

    def add_turn(self, question, answer, slots, data=None, task_type=None):
        self.history.append({
            "question": question,
            "answer": answer[:500],
            "slots": dict(slots),
            "data": data,
            "indicator": slots.get("indicator", ""),
            "task_type": task_type,
            "time": time.time(),
        })
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def get_recent_history(self, n=3):
        return self.history[-n:]

    def is_followup(self, question):
        if len(question) < 8:
            return True
        indicator_keywords = ["净利润", "营收", "收入", "利润", "资产", "负债",
                              "现金流", "每股", "ROE", "毛利", "净利", "同比", "环比"]
        has_indicator = any(kw in question for kw in indicator_keywords)
        has_year = bool(re.search(r"20\d{2}", question))
        return not has_indicator and not has_year

    def merge_question(self, question):
        from time_parser import parse_time
        time_info = parse_time(question)
        new_year = time_info.get("year")
        new_period = time_info.get("period")

        _COMPANY_NAMES = ["白云山", "云南白药", "华润三九", "同仁堂", "太极集团",
            "片仔癀", "济川药业", "天士力", "达仁堂", "瑞康医药", "昆药集团",
            "康恩贝", "葵花药业", "康美药业", "康缘药业", "东阿阿胶", "江中药业",
            "健民集团", "康弘药业", "千金药业", "振东制药", "羚锐制药", "金花股份",
            "桂林三金", "太龙药业", "999", "白药", "金花", "花"]
        _ALIAS = {"999": "华润三九", "三金": "桂林三金", "白云": "白云山",
            "同仁": "同仁堂", "云白": "云南白药", "花": "葵花药业"}
        new_company = None
        for n in _COMPANY_NAMES:
            if n in question:
                new_company = _ALIAS.get(n, n)
                break

        _INDICATOR_KW = {
            "净利润": "net_profit", "营收": "total_operating_revenue",
            "收入": "total_operating_revenue", "利润总额": "total_profit",
            "营业利润": "operating_profit", "毛利率": "gross_profit_margin",
            "净利率": "net_profit_margin", "ROE": "roe",
            "资产负债率": "asset_liability_ratio",
            "研发费用": "operating_expense_rnd_expenses",
            "销售费用": "operating_expense_selling_expenses",
        }
        new_indicator = None
        for kw, col in _INDICATOR_KW.items():
            if kw in question:
                new_indicator = col
                break

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


_manager = None

def get_session_manager():
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
