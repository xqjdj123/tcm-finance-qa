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
        # 理解缓存
        self.last_understanding = None
        # 核心槽位（数组，支持多公司/多指标）
        self.slots = {
            "companies": [],
            "indicators": [],
            "years": [],
            "period": None,
            "top_k": None,
        }

    def update_slots(self, new_slots):
        """更新槽位，只覆盖非空值"""
        for k, v in new_slots.items():
            if v is not None and v != "":
                if k in ("companies", "indicators", "years") and isinstance(v, list):
                    if v:
                        self.slots[k] = v
                elif k in ("company", "indicator", "year"):
                    mapped = {"company": "companies", "indicator": "indicators", "year": "years"}
                    new_k = mapped[k]
                    if v not in self.slots.get(new_k, []):
                        self.slots[new_k] = self.slots.get(new_k, []) + [v]
                else:
                    self.slots[k] = v

    def get_context(self):
        """获取当前有效槽位"""
        return {k: v for k, v in self.slots.items() if v is not None}

    def add_turn(self, question, answer, slots, data=None, task_type=None):
        """保存一轮对话"""
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
        """获取最近n轮对话"""
        return self.history[-n:]

    def get_last_understanding(self):
        return self.last_understanding

    def update_understanding(self, understanding):
        self.last_understanding = understanding
        if understanding.get("companies"):
            self.slots["companies"] = understanding["companies"]
        if understanding.get("indicators"):
            self.slots["indicators"] = understanding["indicators"]
        if understanding.get("years"):
            self.slots["years"] = understanding["years"]
        if understanding.get("period"):
            self.slots["period"] = understanding["period"]
        if understanding.get("top_k"):
            self.slots["top_k"] = understanding["top_k"]

    def get_last_turn(self):
        """获取上一轮对话"""
        return self.history[-1] if self.history else None

    def get_last_data(self):
        """获取上一轮的数据"""
        last = self.get_last_turn()
        return last.get("data") if last else None

    def get_last_slots(self):
        """获取上一轮的槽位"""
        last = self.get_last_turn()
        return last.get("slots", {}) if last else {}

    def get_last_task_type(self):
        """获取上一轮的任务类型"""
        last = self.get_last_turn()
        return last.get("task_type") if last else None


_manager = None

def get_session_manager():
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
