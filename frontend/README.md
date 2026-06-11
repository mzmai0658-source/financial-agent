# 财报 Agent 前端

这个前端是正常使用的财报智能体界面，不再包含固定答题格式或任务模式。它面向自然语言对话：用户直接提问，公司、年份、指标、趋势、原因都可以放在一句话里。

## 技术栈

- Vue 3
- Vite
- TypeScript
- Pinia
- Vue Router
- Ant Design Vue
- ECharts

后端入口：`D:\data_discovery\src\api\main.py`

## 启动方式

先启动 FastAPI：

```powershell
.\.venv\Scripts\python.exe -m src.agent.main
```

再启动前端：

```powershell
cd D:\data_discovery\frontend
npm run dev
```

默认开发地址：

- 前端：[http://localhost:5173](http://localhost:5173)
- 后端：[http://localhost:8000](http://localhost:8000)
- API 文档：[http://localhost:8000/docs](http://localhost:8000/docs)

## 当前能力

- 正常 AI 对话式提问，不需要编号或固定格式
- 后端可自动创建会话，前端同步 `session_id` 和历史消息
- 支持多轮追问和上下文记忆
- SQL、执行步骤、图表、引用和校验信息通过右侧证据抽屉展示
- 缺少年份、报告期、公司或指标时会主动澄清，减少答非所问
- 图表图片和引用页图可直接回显

## 界面原则

- 聊天区优先，过程信息默认收进轻量证据抽屉
- 左侧只保留品牌、能力、示例和服务状态
- 首页和工作台都围绕“自然语言财报分析”组织，不暴露历史批处理格式
