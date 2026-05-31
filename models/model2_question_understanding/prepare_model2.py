# -*- coding: utf-8 -*-
"""
Model 2 - 数据预处理（修正版）
把标注JSON和原始问题合并，生成BART训练格式
"""
import json
import re
import random

random.seed(42)

ANN_PATH = r"D:\HuaweiMoveData\Users\14725\Desktop\question\标注结果_修正版.json"
PROMPT_PATH = r"D:\python-leanrn\codex\data\标注提示_给AI.txt"
OUTPUT_PATH = "model2_data.json"


def read_annotations(path):
    """读取标注结果"""
    with open(path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    return [json.loads(l) for l in lines]


def read_questions(path):
    """从标注提示文件里提取原始问题"""
    questions = []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # 按 --- 分割每条题目
    parts = text.split("---")
    for part in parts:
        for line in part.split("\n"):
            line = line.strip()
            if line.startswith("问题：") or line.startswith("问题:"):
                q = line[3:].strip()
                if q:
                    # 去掉 "(多轮: 继承上文)"
                    q = re.sub(r"\(多轮.*?\)", "", q).strip()
                    questions.append(q)
                    break

    print("读取到 " + str(len(questions)) + " 条原始问题")
    return questions


def format_entry(question, annotation, context):
    """合并问题和标注，生成训练数据"""
    if not question:
        return None

    is_multi = annotation.get("is_multi_turn", False)

    # 构建输入
    if is_multi and context:
        input_text = "上文：" + json.dumps(context, ensure_ascii=False) + "\n"
    else:
        input_text = ""

    input_text += "问题：" + question + "\n输出JSON："

    # 构建输出（只保留有值的字段）
    output = {}
    for key in ["intent", "company", "year", "period", "time_range", "indicator",
                "indicators", "companies", "top_k", "needs_chart", "chart_type",
                "needs_rag", "needs_reasoning", "is_multi_turn", "sub_questions_count"]:
        val = annotation.get(key)
        if val is not None and val != "":
            output[key] = val

    if "condition" in annotation and annotation["condition"]:
        output["condition"] = annotation["condition"]

    # 更新上下文（非多轮时）
    if not is_multi:
        for key in ["company", "indicator", "year", "period"]:
            if key in output:
                context[key] = output[key]

    return {
        "input": input_text,
        "output": json.dumps(output, ensure_ascii=False)
    }


def main():
    print("=" * 60)
    print("Model 2 - 数据预处理")
    print("=" * 60)

    # 读取标注和问题
    annotations = read_annotations(ANN_PATH)
    questions = read_questions(PROMPT_PATH)

    print("标注条数: " + str(len(annotations)))
    print("问题条数: " + str(len(questions)))

    # 合并（按顺序一一对应）
    context = {}
    data = []
    unused_annotations = 0

    for i, ann in enumerate(annotations):
        if i < len(questions):
            q = questions[i]
            result = format_entry(q, ann, context)
            if result:
                data.append(result)
        else:
            unused_annotations += 1

    print("生成训练数据: " + str(len(data)) + " 条")
    if unused_annotations > 0:
        print("未匹配的标注: " + str(unused_annotations) + " 条（问题不够）")

    # 打乱并分割
    random.shuffle(data)
    split = int(len(data) * 0.8)
    train_data = data[:split]
    val_data = data[split:]

    print("训练集: " + str(len(train_data)) + " 条")
    print("验证集: " + str(len(val_data)) + " 条")

    # 保存
    output = {"train": train_data, "val": val_data}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 打印样例
    print("\n=== 样例 ===")
    for i, item in enumerate(train_data[:3]):
        print("\n--- 样例" + str(i + 1) + " ---")
        print("输入: " + item["input"])
        print("输出: " + item["output"])


if __name__ == "__main__":
    main()
