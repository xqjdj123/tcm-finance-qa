# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime

import torch
from sentence_transformers import (
    SentenceTransformer,
    InputExample,
    losses,
    evaluation,
)
from torch.utils.data import DataLoader

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# ============ 配置 ============
DATA_PATH = "training_data.json"
MODEL_NAME = "moka-ai/m3e-base"
OUTPUT_DIR = "output"

BATCH_SIZE = 16
EPOCHS = 10
LEARNING_RATE = 2e-5
WARMUP_STEPS = 100
EVAL_STEPS = 50

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def prepare_examples(data):
    train_examples = []
    eval_examples = []

    pairs = data["sentence_pairs"]
    import random
    random.seed(42)
    random.shuffle(pairs)

    split_idx = int(len(pairs) * 0.8)
    train_pairs = pairs[:split_idx]
    eval_pairs = pairs[split_idx:]

    for p in train_pairs:
        train_examples.append(
            InputExample(texts=[p["text1"], p["text2"]], label=float(p["label"]))
        )

    for p in eval_pairs:
        eval_examples.append(
            InputExample(texts=[p["text1"], p["text2"]], label=float(p["label"]))
        )

    print("训练样本: " + str(len(train_examples)) + ", 验证样本: " + str(len(eval_examples)))
    return train_examples, eval_examples


def print_schema_linking_examples(model, all_columns, test_terms):
    print("\n" + "=" * 60)
    print("Schema Linking 测试结果")
    print("=" * 60)

    column_texts = [c["full_description"] for c in all_columns]
    column_embeddings = model.encode(column_texts, convert_to_tensor=True)

    for term in test_terms:
        query_embedding = model.encode(term, convert_to_tensor=True)
        scores = torch.nn.functional.cosine_similarity(
            query_embedding.unsqueeze(0),
            column_embeddings
        )
        top_indices = scores.argsort(descending=True)[:5]

        print("")
        print("查询词: [" + term + "]")
        for rank, idx in enumerate(top_indices, 1):
            col = all_columns[idx]
            score = scores[idx].item()
            col_str = col["table_name_cn"] + "." + col["column_en"] + ": " + col["description"]
            print("  " + str(rank) + ". [" + "{:.4f}".format(score) + "] " + col_str)


def main():
    print("=" * 60)
    print("Model 1 - Schema Linking 训练")
    print("开始时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    print("\n[1/5] 加载训练数据...")
    data = load_data(DATA_PATH)
    train_examples, eval_examples = prepare_examples(data)

    print("\n[2/5] 加载预训练模型: " + MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME)
    param_count = sum(p.numel() for p in model.parameters())
    print("  模型参数量: " + str(param_count))

    print("\n[3/5] 准备 DataLoader...")
    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=BATCH_SIZE)
    eval_dataloader = DataLoader(eval_examples, shuffle=False, batch_size=BATCH_SIZE)

    print("\n[4/5] 配置训练参数...")
    train_loss = losses.ContrastiveLoss(model=model)

    evaluator = evaluation.BinaryClassificationEvaluator.from_input_examples(
        eval_examples, name="schema-eval"
    )

    print("\n[5/5] 开始训练...")
    print("  Batch Size: " + str(BATCH_SIZE))
    print("  Epochs: " + str(EPOCHS))
    print("  Learning Rate: " + str(LEARNING_RATE))
    print("  输出目录: " + OUTPUT_DIR)

    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        evaluator=evaluator,
        epochs=EPOCHS,
        warmup_steps=WARMUP_STEPS,
        evaluation_steps=EVAL_STEPS,
        output_path=OUTPUT_DIR,
        save_best_model=True,
        optimizer_params={"lr": LEARNING_RATE},
    )

    print("\n训练完成! 模型已保存到: " + OUTPUT_DIR)

    print("\n" + "=" * 60)
    print("加载最佳模型做测试...")
    print("=" * 60)

    best_model = SentenceTransformer(OUTPUT_DIR)
    all_columns = data["all_columns"]

    test_terms = [
        "利润总额", "营业收入", "净利润", "研发费用",
        "每股收益", "净资产收益率", "资产负债率",
        "经营性现金流", "销售费用", "总资产",
        "股票简称", "毛利率", "扣非净利润", "总收入",
    ]

    print_schema_linking_examples(best_model, all_columns, test_terms)

    print("\n" + "=" * 60)
    print("训练完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
