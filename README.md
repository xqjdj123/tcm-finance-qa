# 中药企业财务智能问答系统 🏭📊

> 基于 LLM Agent + RAG 的中药上市公司财务分析智能问答系统  
> 开发周期：2026年4月 — 至今

---

## 📋 项目简介

自然语言驱动的中药企业财务分析助手。用户用中文提问，系统自动理解意图、从 **MySQL 查财报数据**、从 **RAG 索引搜研报**，返回结构化答案。

**可问的问题：**
- *"2024年营收排名前五的中药企业"* → MySQL
- *"片仔癀近三年净利润趋势"* → MySQL
- *"同仁堂有什么经营风险"* → MySQL 风险规则分析
- *"中药行业研发投入趋势"* → RAG 研报

---

## 🏗️ 数据流（两套独立管线）

```
┌─ 财报PDF ─→ pdf_extractor/ ─→ JSON ─→ MySQL（2667行 4张表）──→ sql_tool ─┐
│                                                                           │
│                              Agent ─── 意图识别 ─── 多步工具调用 ───→ 答案  │
│                                                                           │
└─ 研报MD ─→ rag_pipeline/ ─→ FAISS+BM25 索引 ────────→ rag_tool ──────────┘
```

### 管线一：财报提取 → MySQL（已完成 ✅）
- PDF 财报 → `pdf_extractor/`（14个模块）→ 结构化 JSON → 写入 MySQL
- MySQL 已有 **2667 行数据**（资产负债表/利润表/现金流量表/核心指标）
- `sql_tool`、`risk_tool`、`report_tool` 都直接查 MySQL

### 管线二：研报检索 → RAG（代码就绪，索引需本地重建 ⚠️）
- 研报 MD → `rag_pipeline/`（文档清洗+语义分块）→ FAISS + BM25 索引
- `rag_module.py` 封装了双路检索+RRF融合+Reranker
- 研报原文和索引文件未上传 GitHub，需本地重建

---

## 🧩 各模块实际状态

### ✅ 完整可用
| 模块 | 说明 |
|------|------|
| `pdf_extractor/` 14个模块 | 上交所+深交所财报PDF→结构化JSON，含字段映射+LLM回退 |
| `rag_module.py` 21KB | FAISS+BM25双路检索+RRF融合+Reranker |
| `agent/agent.py` | ReAct风格Agent，7类TaskType + 多步工具调用 |
| `agent/tools/sql_tool.py` | MySQL查询，支持趋势/对比/自动模式 |
| `agent/tools/risk_tool.py` | 6条财务风险规则（负债率/现金流/应收账款等） |
| `agent/tools/report_tool.py` | 六段式财务分析报告生成 |
| `agent/tools/data_tool.py` | 二次计算+排名+筛选+统计 |
| `agent/tools/chart_tool.py` | 智能选图(line/bar/hbar/pie) |
| `agent/tools/rag_tool.py` | RAG研报搜索 |
| `app.py` + 前端 | Flask Web 服务 + ECharts 图表 |

### ⚠️ 骨架就绪，模型权重需本地训练
| 模块 | 说明 |
|------|------|
| `models/model2/pipeline.py` | NER+Model2+NL2SQL 查询管线，checkpoint权重未上传 |
| `models/ner_model/` | BertForTokenClassification，路径不存在时会跳过 |
| `models/model1_schema_linking/` | Schema链接（字段→表映射），权重未上传 |

### 🔧 有隐患
- `session.py` — 空文件 0 字节
- `run_app.py` — 通过 .pyc 字节码加载（workaround）
- 数据库密码硬编码 `root/433127hj`
- RAG索引和研报原文不在仓库中，需 `rag_pipeline/build_index.py` 重建

---

## 🛠️ 技术栈

| 组件 | 选型 |
|------|------|
| Web框架 | Flask |
| 数据库 | MySQL（PyMySQL + mysql-connector） |
| LLM | Ollama（本地部署 bge-m3） |
| 向量检索 | FAISS + BM25 |
| NER | BERT + BIO序列标注 |
| 可视化 | ECharts |
| PDF提取 | pdfplumber + 自定义规则 |

---

## 🚀 本地启动（需先准备环境）

```bash
# 1. 安装依赖
pip install flask pymysql mysql-connector-python faiss-cpu numpy requests

# 2. 准备 MySQL（需要本机有 MySQL 服务）
#    数据库名：finance_data
#    账密：root / 433127hj

# 3. 导入财报数据
python pdf_extractor/import_db.py

# 4. 重建 RAG 索引（如果要用研报搜索）
python rag_pipeline/build_index.py

# 5. 启动 Ollama
ollama pull bge-m3

# 6. 运行
python app.py
# 访问 http://127.0.0.1:7860
```

---

## 📓 开发日记（Git Commit 记录）

| 提交 | 内容 |
|------|------|
| `📝 Day 1` | 项目初始化：Agent框架、PDF提取、RAG管线、模型骨架 |

*每次开发完执行 `git commit -m "📝 Day X: ..."` 继续记录*

---

## 📄 许可证

MIT License
