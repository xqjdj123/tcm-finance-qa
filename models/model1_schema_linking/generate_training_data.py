# -*- coding: utf-8 -*-
import json
import random

random.seed(42)

SCHEMA_PATH = "../../data/schema_columns.json"
OUTPUT_PATH = "training_data.json"


def load_schema(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_training_data(schema):
    term_to_columns = {}
    all_columns = []

    for table_name, table_info in schema["tables"].items():
        table_cn = table_info["table_name_cn"]
        for col in table_info["columns"]:
            col_en = col["en"]
            col_cn = col["cn"]
            col_desc = col["desc"]
            aliases = col.get("aliases", [])

            desc_text = table_cn + "." + col_en + ": " + col_desc
            entry = (col_en, table_name, desc_text)
            all_columns.append(entry)

            if col_cn not in term_to_columns:
                term_to_columns[col_cn] = []
            term_to_columns[col_cn].append(entry)

            for alias in aliases:
                if alias not in term_to_columns:
                    term_to_columns[alias] = []
                term_to_columns[alias].append(entry)

    print("词条数: " + str(len(term_to_columns)) + ", 字段数: " + str(len(all_columns)))

    training_data = []
    for term, positive_entries in term_to_columns.items():
        if len(term) < 2:
            continue

        positives = []
        for col_en, table_name, full_desc in positive_entries:
            positives.append({
                "column_en": col_en,
                "table_name": table_name,
                "description": full_desc
            })

        positive_col_ens = set(e[0] for e in positive_entries)
        candidates = [e for e in all_columns if e[0] not in positive_col_ens]

        num_negatives = min(5, len(candidates))
        negatives = random.sample(candidates, num_negatives)
        negative_list = []
        for col_en, table_name, full_desc in negatives:
            negative_list.append({
                "column_en": col_en,
                "table_name": table_name,
                "description": full_desc
            })

        training_data.append({
            "term": term,
            "positives": positives,
            "negatives": negative_list
        })

    return training_data


def build_sentence_pairs(training_data):
    pairs = []
    for item in training_data:
        term = item["term"]
        for pos in item["positives"]:
            pairs.append({"text1": term, "text2": pos["description"], "label": 1})
        for neg in item["negatives"]:
            pairs.append({"text1": term, "text2": neg["description"], "label": 0})
    return pairs


def build_all_column_descriptions(schema):
    columns = []
    for table_name, table_info in schema["tables"].items():
        table_cn = table_info["table_name_cn"]
        for col in table_info["columns"]:
            desc = table_cn + "." + col["en"] + ": " + col["desc"]
            columns.append({
                "column_en": col["en"],
                "table_name": table_name,
                "table_name_cn": table_cn,
                "column_cn": col["cn"],
                "description": col["desc"],
                "full_description": desc
            })
    return columns


if __name__ == "__main__":
    schema = load_schema(SCHEMA_PATH)
    training_data = build_training_data(schema)
    pairs = build_sentence_pairs(training_data)

    output = {
        "training_data": training_data,
        "sentence_pairs": pairs,
        "all_columns": build_all_column_descriptions(schema)
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    pos = sum(1 for p in pairs if p["label"] == 1)
    neg = sum(1 for p in pairs if p["label"] == 0)
    print("保存完成: " + str(len(training_data)) + " 词条, " + str(len(pairs)) + " 样本对 (正:" + str(pos) + ", 负:" + str(neg) + ")")
