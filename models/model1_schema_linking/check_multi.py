import json

path = r'D:\HuaweiMoveData\Users\14725\Desktop\question\标注结果_修正版.json'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

lines = [l.strip() for l in content.split(chr(10)) if l.strip()]
items = []
for line in lines:
    items.append(json.loads(line))

# 找出有问题的 multi_intent
bad = []
for i, item in enumerate(items):
    if item.get('intent') == 'multi_intent':
        has_detail = bool(item.get('indicator') or item.get('indicators') or item.get('company') or item.get('companies'))
        if not has_detail:
            bad.append((i+1, item))

print("需要重新标注: " + str(len(bad)) + " 条")
print()
print("把这部分复制给AI：")
print("=" * 50)
print("请逐条输出JSON，必须包含 company 和 indicator 字段。")
print("=" * 50)
print()

for idx, item in bad:
    print("---")
    print("第" + str(idx) + "条 - 现在标注:")
    print(json.dumps(item, ensure_ascii=False))
    print("请重新输出正确的JSON:")
    print()
