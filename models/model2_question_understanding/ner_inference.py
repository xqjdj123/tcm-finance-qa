# -*- coding: utf-8 -*-
"""NER推理封装 - 加载训练好的BERT序列标注模型进行槽位抽取"""
import json
import os
import torch
from transformers import BertTokenizerFast, BertForTokenClassification

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ner_model", "final")

LABELS = ["O", "B-COMP", "I-COMP", "B-METRIC", "I-METRIC", "B-PERIOD", "I-PERIOD"]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for i, l in enumerate(LABELS)}


class NERExtractor:
    def __init__(self, model_path=None):
        path = model_path or MODEL_PATH
        if not os.path.exists(path):
            print("  NER模型路径不存在: " + path)
            self.model = None
            self.tokenizer = None
            return

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = BertTokenizerFast.from_pretrained(path)
        self.model = BertForTokenClassification.from_pretrained(path).to(self.device)
        self.model.eval()
        print("  NER模型加载完成: " + path)

    def predict(self, text):
        """预测单条文本的BIO标签序列"""
        if not self.model:
            return []
        tokens = list(text)
        encoding = self.tokenizer(
            tokens, truncation=True, is_split_into_words=True,
            max_length=128, padding="max_length", return_tensors="pt"
        ).to(self.device)
        with torch.no_grad():
            outputs = self.model(
                input_ids=encoding["input_ids"],
                attention_mask=encoding["attention_mask"]
            )
        preds = outputs.logits.argmax(dim=-1)[0].cpu().numpy()
        word_ids = encoding.word_ids(0)
        result = []
        prev = None
        for wid, p in zip(word_ids, preds):
            if wid is None or wid == prev:
                continue
            label = ID2LABEL.get(int(p), "O")
            if label != "O":
                result.append((tokens[wid], label))
            prev = wid
        return result

    def extract_slots(self, text):
        """从文本中抽取槽位: {COMP: [...], METRIC: [...], PERIOD: [...]}"""
        tagged = self.predict(text)
        slots = {"COMP": [], "METRIC": [], "PERIOD": []}
        cur_label = None
        cur_text = []
        for token, tag in tagged:
            if tag.startswith("B-"):
                if cur_label and cur_text:
                    slots.setdefault(cur_label, []).append("".join(cur_text))
                cur_label = tag[2:]
                cur_text = [token]
            elif tag.startswith("I-") and cur_label == tag[2:]:
                cur_text.append(token)
            else:
                if cur_label and cur_text:
                    slots.setdefault(cur_label, []).append("".join(cur_text))
                cur_label = None
                cur_text = []
        if cur_label and cur_text:
            slots.setdefault(cur_label, []).append("".join(cur_text))
        return {k: v for k, v in slots.items() if v}


if __name__ == "__main__":
    ner = NERExtractor()
    test_qs = [
        "同仁堂2025利润是多少",
        "金花股份2023年第三季度净利润",
        "白云山和999对比营业总收入",
        "同仁堂资产负债率",
        "存货和营业总收入排名前三的公司"
    ]
    for q in test_qs:
        slots = ner.extract_slots(q)
        print("Q: " + q + "  =>  " + json.dumps(slots, ensure_ascii=False))
