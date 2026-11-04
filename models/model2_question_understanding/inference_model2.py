# -*- coding: utf-8 -*-
"""
Model 2 - 推理脚本（修正版）
"""
import json
import os
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_model2")


class QuestionUnderstandingModel:
    def __init__(self, model_dir=None):
        model_path = model_dir or MODEL_DIR
        if os.path.exists(model_path):
            files = os.listdir(model_path)
            has_model = any(f.endswith(".bin") or f.endswith(".safetensors") or f == "pytorch_model.bin" for f in files)
            if has_model:
                print("加载微调模型: " + model_path)
            else:
                checkpoint_dirs = [f for f in files if f.startswith("checkpoint-") and os.path.isdir(os.path.join(model_path, f))]
                if checkpoint_dirs:
                    model_path = os.path.join(model_path, sorted(checkpoint_dirs)[-1])
                    print("加载微调模型: " + model_path)
                else:
                    print("未找到微调模型文件，加载预训练模型")
                    print("请先运行 train_model2.py")
                    model_path = "fnlp/bart-base-chinese"
        else:
            print("未找到微调模型，加载预训练模型")
            model_path = "fnlp/bart-base-chinese"
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_path)
        self.context = {}
        print("Model 2 初始化完成!")

    def understand(self, question, is_multi_turn=False):
        import re
        if is_multi_turn and self.context:
            input_text = "上文：" + json.dumps(self.context, ensure_ascii=False) + "\n"
        else:
            input_text = ""
        input_text += "问题：" + question + "\n输出JSON："
        inputs = self.tokenizer(input_text, return_tensors="pt", max_length=256, truncation=True)
        if "token_type_ids" in inputs:
            del inputs["token_type_ids"]

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_length=128,
                num_beams=4,
                early_stopping=True,
            )
        raw = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        t = raw.replace(' ', '')
        t = t.replace('，', ',').replace('：', ':').replace('“', '"')
        t = t.replace('”', '"').replace('「', '"').replace('」', '"')
        t = t.replace('《', '"').replace('》', '"')
        t = t.replace('True', 'true').replace('False', 'false')
        t = t.replace('[', '{').replace(']', '}')
        t = re.sub(r':([一-鿿]+)"', r':"\1"', t)
        t = re.sub(r'":(\d+)"', r'":\1', t)
        t = re.sub(r'year:(\d+),', r'"year":\1,', t)

        intent_m = re.search(r'"intent":\s*"([^"]+)"', t)
        intent = intent_m.group(1) if intent_m else "unknown"
        result = {"intent": intent}

        c = re.search(r'"company":\s*"([^"]+)"', t)
        if not c:
            c = re.search(r'"company":([\u4e00-\u9fff]+)', t)
        if c:
            company_raw = c.group(1).replace('"', '')
            for suffix in ["近三年", "近五年", "去年", "今年"]:
                if suffix in company_raw:
                    company_raw = company_raw[:company_raw.index(suffix)]
            result["company"] = company_raw

        y = re.search(r'"year":\s*(\d+)', t)
        if y:
            result["year"] = int(y.group(1))
        p = re.search(r'"period":\s*"([^"]+)"', t)
        if p:
            result["period"] = p.group(1)

        ind = re.search(r'"indicator":\s*"([^"]+)"', t)
        if not ind:
            ind = re.search(r'"indicator":([\u4e00-\u9fff]+)', t)
        if ind:
            result["indicator"] = ind.group(1).replace('"', '')

        for key in ["needs_chart", "needs_rag", "is_multi_turn"]:
            m = re.search(r'"' + key + r'":\s*(true|false)', t)
            if m:
                result[key] = m.group(1) == 'true'
        ct = re.search(r'"chart_type":\s*"([^"]+)"', t)
        if ct:
            result["chart_type"] = ct.group(1)
        tk = re.search(r'"top_k":\s*(\d+)', t)
        if tk:
            result["top_k"] = int(tk.group(1))

        if not is_multi_turn:
            for key in ["company", "indicator", "year", "period"]:
                if key in result:
                    self.context[key] = result[key]
        if is_multi_turn and self.context:
            for key in ["company", "indicator", "year", "period"]:
                if key not in result and key in self.context:
                    result[key] = self.context[key]
        return result

    def reset_context(self):
        self.context = {}


if __name__ == "__main__":
    matcher = QuestionUnderstandingModel()
    questions = ["金花股份2023年利润总额是多少"]
    for i, q in enumerate(questions):
        print(chr(10) + "=" * 60)
        print("问题" + str(i + 1) + ": " + q)
        result = matcher.understand(q)
        print("理解结果: " + json.dumps(result, ensure_ascii=False))
