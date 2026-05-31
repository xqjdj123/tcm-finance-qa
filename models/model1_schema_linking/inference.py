# -*- coding: utf-8 -*-
import json
import os
import torch
from sentence_transformers import SentenceTransformer

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "schema_columns.json")
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


class FinancialSchemaMatcher:
    def __init__(self, model_dir=None):
        model_path = model_dir or MODEL_DIR
        if os.path.exists(model_path) and os.path.exists(os.path.join(model_path, "config.json")):
            print("加载微调模型: " + model_path)
            self.model = SentenceTransformer(model_path)
        else:
            print("未找到微调模型，加载预训练 m3e-base")
            print("请先运行 train.py 进行训练")
            self.model = SentenceTransformer("moka-ai/m3e-base")

        with open(DATA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)

        self.columns = []
        for table_name, table_info in schema["tables"].items():
            table_cn = table_info["table_name_cn"]
            for col in table_info["columns"]:
                desc = table_cn + "." + col["en"] + ": " + col["desc"]
                self.columns.append({
                    "column_en": col["en"],
                    "table_name": table_name,
                    "table_name_cn": table_cn,
                    "column_cn": col["cn"],
                    "description": col["desc"],
                    "full_description": desc
                })

        print("编码 " + str(len(self.columns)) + " 个字段描述...")
        column_texts = [c["full_description"] for c in self.columns]
        self.column_embeddings = self.model.encode(
            column_texts, convert_to_tensor=True
        )
        self.column_texts = column_texts
        print("初始化完成!")

    def match(self, term, top_k=5):
        query_embedding = self.model.encode(term, convert_to_tensor=True)

        scores = torch.nn.functional.cosine_similarity(
            query_embedding.unsqueeze(0),
            self.column_embeddings
        )

        top_indices = scores.argsort(descending=True)[:top_k]

        results = []
        for idx in top_indices:
            col = self.columns[idx]
            score = scores[idx].item()
            results.append({
                "column_en": col["column_en"],
                "table_name": col["table_name"],
                "table_name_cn": col["table_name_cn"],
                "column_cn": col["column_cn"],
                "description": col["description"],
                "score": round(score, 4),
                "display": col["full_description"]
            })

        return results

    def match_batch(self, terms, top_k=3):
        return {term: self.match(term, top_k) for term in terms}

    def match_from_question(self, question):
        candidates = set()
        all_cn_terms = set(c["column_cn"] for c in self.columns)
        skip_terms = {"序号", "股票代码", "股票简称", "报告期", "报告期年份"}
        all_cn_terms = all_cn_terms - skip_terms

        for term in all_cn_terms:
            if term in question and len(term) >= 2:
                candidates.add(term)

        results = {}
        for term in candidates:
            matches = self.match(term, top_k=1)
            if matches:
                results[term] = matches[0]

        return results


if __name__ == "__main__":
    model_dir = None
    if os.path.exists(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        model_dir = MODEL_DIR

    matcher = FinancialSchemaMatcher(model_dir)

    print("\n" + "=" * 60)
    print("单个术语匹配测试")
    print("=" * 60)

    test_terms = [
        "利润总额", "营业收入", "净利润", "研发费用",
        "每股收益", "净资产收益率", "资产负债率",
        "经营性现金流", "毛利率", "总资产",
        "扣非净利润", "短期借款", "销售费用", "管理费用"
    ]

    for term in test_terms:
        results = matcher.match(term, top_k=3)
        print("")
        print("[" + term + "]")
        for r in results:
            print("  " + str(r["score"]) + " | " + r["display"])

    print("\n" + "=" * 60)
    print("从问题中提取并匹配")
    print("=" * 60)

    questions = [
        "金花股份2023年利润总额是多少",
        "2025年第三季度研发费用占比前五的公司",
        "千金药业近3年的收入趋势",
        "哪些企业资产负债率超过70%"
    ]

    for q in questions:
        print("")
        print("问题: " + q)
        matches = matcher.match_from_question(q)
        for term, match in matches.items():
            print("  " + term + " -> " + match["display"] + " (score: " + str(match["score"]) + ")")
