# Figma / Design Notes

这个文档记录当前财报 Agent 前端的设计方向，方便后续同步到 Figma 或继续演进组件库。

## 页面结构

建议 Figma 文件保留 4 个页面：

1. `Foundations`
2. `Components`
3. `Patterns`
4. `Screens`

## 视觉方向

- 风格：克制的投研工作台，聊天优先，证据次之
- 背景：暖白纸感底色，叠加轻微蓝灰和铜金色氛围光
- 主色：深墨蓝，用于对话主按钮、用户气泡和品牌底色
- 辅色：温和铜金，用于重点状态、选中态和品牌符号
- 避免：暗黑大屏、紫色渐变、密集卡片墙、过大的过程面板

## 设计 Token

### Color

- `bg.canvas = #f3efe6`
- `bg.rail = #111c2d`
- `surface.default = #ffffff`
- `surface.soft = #f8f5ee`
- `text.primary = #172033`
- `text.secondary = #687489`
- `brand.ink = #17243a`
- `brand.bronze = #a96f34`
- `status.success = #237b5d`
- `status.warning = #c2872f`
- `status.error = #b64c3e`

### Radius

- `radius.md = 14`
- `radius.lg = 22`
- `radius.xl = 30`

### Spacing

- `space.2 = 8`
- `space.3 = 12`
- `space.4 = 16`
- `space.6 = 24`
- `space.8 = 32`

## 组件清单

1. `QuestionInput`
2. `ConversationBubble/User`
3. `ConversationBubble/Assistant`
4. `EvidenceDrawer`
5. `ExecutionPlanPanel`
6. `SqlPreviewCard`
7. `ChartPanel`
8. `ReferenceCard`
9. `SystemStatusBar`
10. `ExampleQuestionButton`

## 屏幕状态

1. `Home / Default`
2. `Workspace / Empty`
3. `Workspace / Loading`
4. `Workspace / Answer with SQL`
5. `Workspace / Clarification`
6. `Workspace / Evidence Drawer Open`
7. `Workspace / Error`

## 交互要求

- 对话是主任务，输入框始终贴近底部并保持足够宽度
- 右侧证据抽屉默认折叠，点击助手消息或“证据”按钮后展开
- 证据抽屉包含 SQL、执行步骤、图表、引用和校验信息
- 澄清问题必须留在聊天流中，并在顶栏展示轻量提示
- 移动端隐藏左侧栏，证据抽屉以覆盖层展示

## 与代码映射

- 首页：`D:\data_discovery\frontend\src\pages\HomePage.vue`
- 工作台：`D:\data_discovery\frontend\src\pages\WorkspacePage.vue`
- 组件目录：`D:\data_discovery\frontend\src\components`
- 全局样式：`D:\data_discovery\frontend\src\styles.css`
