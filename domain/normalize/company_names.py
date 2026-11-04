# -*- coding: utf-8 -*-
"""公司名/别名/代码 canonical 化 —— **唯一事实源**

## 为什么要有这个文件
本模块由 `agent/call_memory.py` 依赖（结果指纹 canonical 化）。历史上
`COMPANY_NAMES` 在项目里有 **5 份拷贝**，各自维护、逐次漂移：

| 拷贝位置 | 条数 | 备注 |
|---|---|---|
| `agent/tools/sql_tool.py`（方法内局部变量） | 41 | 关键词兜底用 |
| `rag_pipeline/config.py` | 46 | 建索引时从正文认公司 |
| `models/model2_question_understanding/pipeline.py` | 70 | 抽不到公司时的字典兜底 |
| `models/model2_question_understanding/session.py` | 30 | 已随该文件删除（硬编码多轮，死代码） |
| 本文件 | 41 | 只被 call_memory 用 |

**2026-09-14 收敛**：用 AST 解析上述全部拷贝求并集（脚本
`.workbuddy/tmp_probes/20260914/_collect_companies.py`），得到 78 条表层词，
其中 6 条实为别名（`999 / 白药 / 白云 / 同仁 / 金花 / 花`）已移入 `ALIASES`，
最终 **72 个公司名 + 18 条别名**。并集校验：全部旧拷贝的每个词都能在新表里找到
（见 `verify/verify_no_hardcode.py`）。

**禁止再新增拷贝** —— 需要公司名/别名一律 `from domain.normalize.company_names import ...`。

## 为什么是"代码内常量"而不是"从 DB 读"
这属于**领域词表**：DB 的 `stock_abbr` 只覆盖 70 家财报主体，而研报语料里出现的
公司（含华润双鹤、科伦药业等）不在财报库中，纯靠 DB 会漏。故本表是"语料侧口径"，
与 DB 口径**并存但不重复**：`_CODE_TO_NAME` 留作 DB 回填的扩展点。
"""
import re

# ============================================================
# 公司简称（72 家；并集自 5 份历史拷贝，见模块 docstring）
# ============================================================
COMPANY_NAMES = [
    # --- 财报库主体（第一批，原 SSOT 顺序）---
    "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
    "金花股份", "葵花药业", "达仁堂", "天士力", "康恩贝", "东阿阿胶",
    "江中药业", "健民集团", "千金药业", "羚锐制药", "济川药业",
    "昆药集团", "康美药业", "康缘药业", "康弘药业", "振东制药",
    "桂林三金", "太龙药业", "以岭药业", "步长制药", "信邦制药",
    "红日药业", "仁和药业", "珍宝岛", "中恒集团", "马应龙",
    "亚宝药业", "九芝堂", "益佰制药", "众生药业", "广誉远",
    "贵州三力", "方盛制药", "奇正藏药", "佐力药业",
    # --- 仅见于 pipeline.py 拷贝（模型兜底路径曾用）---
    "瑞康医药", "新里程", "神奇制药", "精华制药", "万邦德", "通化金马",
    "康泰医学", "盘龙药业", "华神科技", "新天药业", "恩威医药", "华森制药",
    "沃华医药", "寿仙谷", "特一药业", "益盛药业", "康惠制药", "嘉应制药",
    "维康药业", "启迪药业", "粤万年青", "新光药业", "天目药业",
    "莱茵生物", "陇神戎发",
    # --- 仅见于 rag_pipeline 拷贝（研报语料侧曾有）---
    "华润双鹤", "西藏药业", "康芝药业", "香雪制药", "科伦药业", "贵州百灵",
]

# ============================================================
# 别名 → 标准简称
# ============================================================
# 分三类来源，语义不同、**合并时不得相互覆盖**：
#   1) 股票代码尾号 / 简称（正文偶用）：999 / 双鹤 / 538 / 436 / 085
#   2) 隐晦称呼（研报正文深度匹配）：民族瑰宝 / 精品国药 / 百年传承 / 云药
#   3) 常用省称：三金 / 云白 / 白云 / 同仁 / 白药 / 花 / 金花
ALIASES = {
    # --- 代码尾号 ---
    "999": "华润三九",
    "538": "云南白药",
    "436": "片仔癀",
    "085": "同仁堂",
    # --- 简称 ---
    "三九": "华润三九",
    "双鹤": "华润双鹤",
    "三金": "桂林三金",
    "云白": "云南白药",
    "白云": "白云山",
    "同仁": "同仁堂",
    "白药": "云南白药",
    "金花": "金花股份",
    # --- 隐晦称呼 ---
    "民族瑰宝": "片仔癀",
    "精品国药": "片仔癀",
    "片仔癀药业": "片仔癀",
    "百年传承": "云南白药",
    "云药": "云南白药",
    # --- 单字省称（**仅精确匹配可用**，禁止参与子串扫描，见 find_all）---
    "花": "葵花药业",
}

# 代码 → 简称（二期从 DB stock_abbr / stock_code 回填；一期为空，禁止臆造）
_CODE_TO_NAME = {}

# 子串扫描的最短长度：单字别名（如「花」）放进子串匹配会误命中
# 「花费」「花卉」等日常词，故仅保留给 canonical() 精确匹配。
_MIN_SCAN_LEN = 2


def canonical(name_or_code):
    """把公司简称/别名/代码/代码尾号归一到标准简称。

    无法识别时**去空白后原样返回**（不臆造、不做模糊匹配）。
    返回空串代表输入为空。
    """
    if not name_or_code:
        return ""
    s = str(name_or_code).strip()
    if not s:
        return ""
    if s in _CODE_TO_NAME:
        return _CODE_TO_NAME[s]
    if s in ALIASES:
        return ALIASES[s]
    return re.sub(r"[\s　]+", "", s)


def _scan_surfaces():
    """子串扫描候选：[(表层词, 标准名)]，长词在前（保证「云南白药」赢过「白药」）。"""
    pairs = [(n, n) for n in COMPANY_NAMES]
    pairs += [(a, s) for a, s in ALIASES.items() if len(a) >= _MIN_SCAN_LEN]
    pairs.sort(key=lambda kv: -len(kv[0]))
    return pairs


def find_all(text):
    """扫描文本中出现的**全部**公司（最长优先、去重叠、按出现位置返回）。

    - 最长优先：`云南白药` 命中后，`白药` 不再重复计一次（掩码重叠区）；
    - 顺序：按在文本中首次出现的位置，供「A 和 B 对比」类问题保持主客顺序；
    - 无命中返回 `[]`；任何非字符串输入安全返回 `[]`。
    """
    if not text or not isinstance(text, str):
        return []
    taken = [False] * len(text)
    hits = []
    for surface, canon in _scan_surfaces():
        start = 0
        while True:
            i = text.find(surface, start)
            if i < 0:
                break
            if not any(taken[i:i + len(surface)]):
                for j in range(i, i + len(surface)):
                    taken[j] = True
                hits.append((i, canon))
            start = i + 1
    hits.sort(key=lambda p: p[0])
    out = []
    for _, canon in hits:
        if canon not in out:
            out.append(canon)
    return out


def find_first(text):
    """只取第一个（按出现位置）；无命中返回 None。"""
    found = find_all(text)
    return found[0] if found else None


def all_names():
    """标准公司名清单的副本（防调用方原地改坏真相源）。"""
    return list(COMPANY_NAMES)


def all_aliases():
    """别名表的副本。"""
    return dict(ALIASES)


if __name__ == "__main__":
    tests = [
        "云南白药2024年净利润",
        "云南白药和片仔癀2024年营收对比",
        "白云山2024年营收",
        "999的营收",
        "那净利润呢",                 # 无公司 → []
        "花费很大",                   # 单字别名「花」不得误命中
        "华润双鹤与科伦药业对比",
        "喀什地区的药企",              # 干扰
    ]
    for t in tests:
        print(f"{t}  ->  {find_all(t)}")
