# 财报智能体（Financial Report Agent）

面向上市公司财报的对话式分析系统：自然语言提问 → LLM 自主规划工具调用（SQL 直查 / 研报检索 / 图表生成）→ 流式输出带证据的回答。

## 架构

```
frontend/  Vue 3 + Vite + Pinia（chat-first 界面，SSE 流式渲染，右侧证据栏）
src/api/   FastAPI（/api/chat/stream SSE、会话存储）
src/agent/ Agent 核心
  ├─ orchestrator.py   事件流主循环（LLM function calling，多轮工具链）
  ├─ prompts.py        系统提示词 + 工具 schema
  ├─ sql_guard.py      SQL 只读防护（白名单 + LIMIT 强制）
  ├─ domain.py         schema/公司表单一事实来源（从 init_db 模型自动生成）
  ├─ fallback.py       LLM 不可用时的规则兜底
  ├─ tools.py          SQLTool / RAGTool / ChartTool
  └─ llm_client.py     DeepSeek 客户端（流式 / function calling / JSON 模式）
src/etl/   离线 ETL：PDF 解析入库（boss/etl_worker）、RAG 知识库构建（rag_builder）
src/utils/ ETL 支撑：OCR 转换/解析、公司注册表、数据路径、同比计算等
scripts/   ETL 入口（reimport_data / convert_research_reports / rebuild_kb）、冒烟测试（smoke_chat）
tests/     离线行为契约测试（FakeLLM，不依赖外部服务）
```

## 准备

1. 依赖：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd frontend
npm install
cd ..
```

2. 环境变量：复制 `.env.example` 为 `.env` 并填入 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、MySQL 配置。

3. 数据（首次）：

```powershell
.venv\Scripts\python.exe -m src.init_db                       # 建库建表
.venv\Scripts\python.exe scripts\reimport_data.py             # 财报 PDF 解析入库（含同比增长率回填）
.venv\Scripts\python.exe scripts\convert_research_reports.py  # 研报 PDF OCR 转 JSON
.venv\Scripts\python.exe scripts\rebuild_kb.py                # 构建 RAG 知识库
```

## 启动

PowerShell 不支持 `&&`，请分开执行（或开两个终端）：

```powershell
# 终端 1：后端（默认 :8000）
.venv\Scripts\python.exe -m src.agent.main
```

```powershell
# 终端 2：前端（默认 :5173）
cd frontend
npm run dev
```

打开 http://localhost:5173 即可使用。

## 测试

```powershell
# 离线测试套件（FakeLLM，不需要外部服务）
.venv\Scripts\python.exe -m pytest tests -q

# 真实 LLM 端到端冒烟（需要 API Key + MySQL + Chroma）
.venv\Scripts\python.exe scripts\smoke_chat.py
.venv\Scripts\python.exe scripts\smoke_chat.py "药明康德2024年净利润是多少"
```

## API 速览

| 接口 | 说明 |
| --- | --- |
| `POST /api/chat/stream` | SSE 流式问答（`plan` / `tool_call` / `tool_result` / `answer_delta` / `chart` / `clarify` / `done`） |
| `POST /api/chat/query` | 非流式问答（聚合后一次性返回） |
| `POST /api/sessions` · `GET/DELETE /api/sessions/{id}` | 会话管理 |
| `GET /api/health` | 服务 / 数据库 / 知识库 / LLM 健康状态 |
| `GET /api/examples` | 示例问题 |

## 注意事项

- LLM 生成的 SQL 必须通过 `sql_guard` 只读校验（单条 SELECT/WITH、表白名单、强制 LIMIT）。
- LLM 不可用时自动降级规则兜底（`fallback.py`），回答会注明降级状态。
- 会话持久化在 `data/runtime/chat_sessions.json`，最多保留 100 个会话。
- CORS 当前为 `*`，`/api/assets` 可读取 workspace 内文件，仅适合本地使用，对外部署前需收紧。
