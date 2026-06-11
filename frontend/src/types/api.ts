export type MessageRole = "user" | "assistant" | "system";

export interface StatusItem {
  ok: boolean;
  detail: string;
}

export interface HealthResponse {
  service: StatusItem;
  database: StatusItem;
  knowledge_base: StatusItem;
  llm: StatusItem;
  examples: StatusItem;
}

export interface ExampleItem {
  id: string;
  type: string;
  question: string;
}

export interface ExamplesResponse {
  examples: ExampleItem[];
}

export interface ReferenceItem {
  paper_path: string;
  source_title: string;
  text: string;
  score?: number | null;
  paper_image?: string | null;
}

export interface ChartData {
  chart_type: string;
  title: string;
  x_label?: string;
  y_label?: string;
  x_data: string[];
  y_data: number[];
  series_name?: string;
}

export interface ExecutionStep {
  step?: number;
  label?: string;
  detail?: string;
  status?: string;
}

export interface AssistantMetadata {
  sql: string;
  chart_format: string;
  chart_data?: ChartData | null;
  chart_data_list?: ChartData[];
  images: string[];
  references: ReferenceItem[];
  validation: Record<string, unknown>;
  execution_plan: ExecutionStep[];
  needs_clarification: boolean;
  clarify_options?: string[];
  context: Record<string, unknown>;
}

export interface SessionMessage {
  role: MessageRole;
  content: string;
  ts: string;
  metadata?: AssistantMetadata | null;
}

export interface SessionResponse {
  session_id: string;
  messages: SessionMessage[];
  latest_context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ChatQueryResponse {
  session_id: string;
  answer: {
    content: string;
    image: string[];
    references: ReferenceItem[];
  };
  sql: string;
  chart_format: string;
  chart_data?: ChartData | null;
  chart_data_list?: ChartData[];
  execution_plan: ExecutionStep[];
  validation: Record<string, unknown>;
  context: Record<string, unknown>;
  needs_clarification: boolean;
  clarify_options?: string[];
  messages: SessionMessage[];
}

/* ── SSE 流式事件 ───────────────────────────────────────────── */

export type StreamEventType =
  | "session"
  | "plan"
  | "tool_call"
  | "tool_result"
  | "answer_delta"
  | "chart"
  | "references"
  | "clarify"
  | "error"
  | "done";

export interface StreamSessionEvent {
  session_id: string;
}

export interface StreamPlanEvent {
  label: string;
  detail?: string;
}

export interface StreamToolCallEvent {
  tool: string;
  label: string;
  detail?: string;
}

export interface StreamToolResultEvent {
  tool: string;
  status: string;
  summary?: string;
  sql?: string;
  row_count?: number | null;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  items?: Array<Partial<ReferenceItem>>;
}

export interface StreamAnswerDeltaEvent {
  text: string;
}

export interface StreamChartEvent {
  path?: string;
  url?: string;
  chart_data?: ChartData | null;
  title?: string;
}

export interface StreamClarifyEvent {
  question: string;
  options?: string[];
}

export interface StreamErrorEvent {
  message: string;
}

export interface StreamHandlers {
  onSession?: (event: StreamSessionEvent) => void;
  onPlan?: (event: StreamPlanEvent) => void;
  onToolCall?: (event: StreamToolCallEvent) => void;
  onToolResult?: (event: StreamToolResultEvent) => void;
  onAnswerDelta?: (event: StreamAnswerDeltaEvent) => void;
  onChart?: (event: StreamChartEvent) => void;
  onClarify?: (event: StreamClarifyEvent) => void;
  onError?: (event: StreamErrorEvent) => void;
  onDone?: (event: ChatQueryResponse) => void;
}
