# 财报智能体（Financial Report Agent）

面向上市公司财报的对话式分析系统：自然语言提问，后端 agent 自主调用 SQL 查询、研报/年报检索和图表生成工具，并通过 SSE 流式返回答案。

## 目录结构

```text
frontend/   Vue 3 + Vite + Pinia 聊天界面
src/api/    FastAPI 对话接口、SSE 流式输出、会话存储
src/agent/  Agent 编排、提示词、SQL 安全校验、工具调用、兜底逻辑
src/etl/    离线数据处理和知识库构建脚本
database/   可直接导入的 MySQL 数据库 SQL
scripts/    数据导入、知识库构建、真实链路冒烟脚本
tests/      离线行为测试
```

## 环境准备

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

cd frontend
npm install
cd ..
```

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 中填写：

```text
DEEPSEEK_API_KEY=你的模型 API Key
DASHSCOPE_API_KEY=你的 DashScope Key（用于向量检索，可选）
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
# 在这里填写你的本机 MySQL 密码
DB_PASSWORD=
DB_NAME=financial_report
```

## 导入数据库

仓库已包含课程作业用的小型 MySQL 导出文件：

```text
database/financial_report.sql
```

在已安装 MySQL 的电脑上执行：

```powershell
mysql --default-character-set=utf8mb4 -u root -p < database\financial_report.sql
```

导入后会得到 `financial_report` 数据库，包含 4 张表：

```text
income_sheet
balance_sheet
cash_flow_sheet
core_performance_indicators_sheet
```

每张表包含 120 条示例数据。导入完成后确认 `.env` 中的 MySQL 用户名和密码与本机一致即可。

## 启动

后端：

```powershell
.venv\Scripts\python.exe -m src.agent.main
```

前端：

```powershell
cd frontend
npm run dev
```

打开：

```text
http://localhost:5173
```

## 测试

```powershell
.venv\Scripts\python.exe -m pytest tests -q

cd frontend
npm run build
```

真实链路冒烟测试（需要 API Key 和 MySQL）：

```powershell
.venv\Scripts\python.exe scripts\smoke_chat.py "药明康德2024年净利润是多少"
```

## API

| 接口 | 说明 |
| --- | --- |
| `GET /api/health` | 服务、数据库、知识库、LLM 状态 |
| `GET /api/examples` | 示例问题 |
| `POST /api/sessions` | 创建会话 |
| `GET /api/sessions/{id}` | 获取会话 |
| `DELETE /api/sessions/{id}` | 删除会话 |
| `POST /api/chat/stream` | SSE 流式问答 |
| `POST /api/chat/query` | 非流式问答 |

## 说明

- SQL 由 `src/agent/sql_guard.py` 做只读校验，只允许单条 `SELECT/WITH` 查询。
- 如果 LLM 不可用，系统会使用规则兜底查询常见的公司、年份、报告期和指标问题。
- 会话本地持久化在 `data/runtime/chat_sessions.json`，不会提交到 Git。
- `database/financial_report.sql` 只包含课程作业演示所需的小型结构化数据，不包含 API Key 或本机密码。

