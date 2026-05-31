# -*- coding: utf-8 -*-
"""
Model 2 - BART-base-chinese 微调训练
将中文问题转化为结构化JSON理解
"""
import json
import os
import re
import random

from datasets import Dataset
import torch
from transformers import (
    AutoTokenizer,
    AutoModelForSeq2SeqLM,
    Seq2SeqTrainingArguments,
    Seq2SeqTrainer,
    DataCollatorForSeq2Seq,
)

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# ============ 配置 ============
DATA_PATH = "金融问答标注数据集1500.json"
MODEL_NAME = "fnlp/bart-base-chinese"
OUTPUT_DIR = "output_model2"

MAX_INPUT_LENGTH = 256
MAX_OUTPUT_LENGTH = 128
BATCH_SIZE = 8
EPOCHS = 20
LEARNING_RATE = 3e-5

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        all_data = json.load(f)

    # 打乱
    random.seed(42)
    random.shuffle(all_data)

    # 80% 训练，20% 验证
    split = int(len(all_data) * 0.8)
    train_data = all_data[:split]
    val_data = all_data[split:]

    print("总条数: " + str(len(all_data)))
    print("训练集: " + str(len(train_data)) + " 条")
    print("验证集: " + str(len(val_data)) + " 条")
    print()

    # 打印几个样例
    for i, item in enumerate(train_data[:2]):
        print("样例" + str(i + 1) + ":")
        print("  输入: " + item["input"][:60] + "...")
        print("  输出: " + item["output"])
    print()

    return {
        "train": train_data,
        "val": val_data
    }


def preprocess_function(examples, tokenizer):
    """对数据集进行tokenize"""
    inputs = tokenizer(
        examples["input"],
        max_length=MAX_INPUT_LENGTH,
        truncation=True,
        padding=False,
    )

    with tokenizer.as_target_tokenizer():
        labels = tokenizer(
            examples["output"],
            max_length=MAX_OUTPUT_LENGTH,
            truncation=True,
            padding=False,
        )

    inputs["labels"] = labels["input_ids"]
    return inputs


def compute_metrics(eval_preds, tokenizer):
    preds, labels = eval_preds

    # 只做 loss 统计，不做文本解码（避免解码错误）
    total = len(labels)

    # 简单统计：只看有多少预测结果能解析出JSON
    json_valid = 0
    intent_correct = 0
    exact_match = 0

    # 安全解码
    import numpy as np
    for pred, label in zip(preds, labels):
        try:
            # 过滤掉 -100
            label = [t for t in label if t != -100]

            # 跳过空的label
            if not label:
                continue

            pred_text = tokenizer.decode(
                [max(0, t) for t in pred if t >= 0],
                skip_special_tokens=True
            )
            label_text = tokenizer.decode(
                [max(0, t) for t in label],
                skip_special_tokens=True
            )

            if pred_text.strip() == label_text.strip():
                exact_match += 1

            try:
                pred_json = json.loads(pred_text)
                json_valid += 1
                label_json = json.loads(label_text)
                if pred_json.get("intent") == label_json.get("intent"):
                    intent_correct += 1
            except:
                pass
        except:
            pass

    metrics = {}
    if total > 0:
        metrics["json_valid_rate"] = json_valid / total
        metrics["intent_accuracy"] = intent_correct / total
        metrics["exact_match"] = exact_match / total

    return metrics


def main():
    print("=" * 60)
    print("Model 2 - BART-base 微调训练")
    print("模型: " + MODEL_NAME)
    print("=" * 60)

    # 1. 加载数据
    print("\n[1/5] 加载数据...")
    data = load_data(DATA_PATH)

    train_data = data["train"]
    val_data = data["val"]
    print("训练集: " + str(len(train_data)) + " 条")
    print("验证集: " + str(len(val_data)) + " 条")

    # 2. 加载tokenizer和模型
    print("\n[2/5] 加载模型和tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)

    param_count = sum(p.numel() for p in model.parameters())
    print("模型参数量: " + str(param_count))

    # 3. 构建Dataset
    print("\n[3/5] 构建数据集...")

    train_dataset = Dataset.from_list(train_data)
    val_dataset = Dataset.from_list(val_data)

    train_dataset = train_dataset.map(
        lambda x: preprocess_function(x, tokenizer),
        batched=False,
        remove_columns=train_dataset.column_names,
    )
    val_dataset = val_dataset.map(
        lambda x: preprocess_function(x, tokenizer),
        batched=False,
        remove_columns=val_dataset.column_names,
    )

    # 4. 配置训练参数
    print("\n[4/5] 配置训练参数...")

    data_collator = DataCollatorForSeq2Seq(
        tokenizer=tokenizer,
        model=model,
        padding=True,
    )

    training_args = Seq2SeqTrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        num_train_epochs=EPOCHS,
        learning_rate=LEARNING_RATE,
        warmup_steps=50,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        #predict_with_generate=True,
        generation_max_length=MAX_OUTPUT_LENGTH,
        load_best_model_at_end=True,
        metric_for_best_model="intent_accuracy",
        greater_is_better=True,
        report_to="none",
    )

    # 5. 开始训练
    print("\n[5/5] 开始训练...")
    print("Batch Size: " + str(BATCH_SIZE))
    print("Epochs: " + str(EPOCHS))
    print("Learning Rate: " + str(LEARNING_RATE))

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=data_collator,
        tokenizer=tokenizer,
        compute_metrics=lambda p: compute_metrics(p, tokenizer),
    )

    trainer.train()

    print("\n训练完成!")
    print("最佳模型已保存到: " + OUTPUT_DIR)


if __name__ == "__main__":
    main()
