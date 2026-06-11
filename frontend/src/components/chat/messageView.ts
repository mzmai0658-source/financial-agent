import type { LiveChart, LiveStep } from "@/stores/session";
import type { ReferenceItem, SessionMessage } from "@/types/api";

/** 消息渲染视图模型：静态历史消息与流式进行中消息共用同一渲染路径。 */
export interface MessageView {
  role: "user" | "assistant";
  content: string;
  live: boolean;
  steps: LiveStep[];
  sql: string;
  rowCount?: number | null;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  charts: LiveChart[];
  references: Array<Partial<ReferenceItem>>;
  needsClarification: boolean;
  clarifyOptions: string[];
  error: string;
  /** messages 数组中的下标，用于证据栏联动；流式消息为 -1 */
  messageIndex: number;
}

export function viewFromMessage(message: SessionMessage, messageIndex: number): MessageView {
  const meta = message.metadata ?? null;
  const steps: LiveStep[] = (meta?.execution_plan ?? []).map((step) => ({
    tool: "plan",
    label: String(step.label ?? ""),
    detail: String(step.detail ?? ""),
    status: step.status === "waiting" ? "empty" : step.status === "error" ? "error" : "done",
  }));

  const charts: LiveChart[] = [];
  const chartDataList = meta?.chart_data_list?.length
    ? meta.chart_data_list
    : meta?.chart_data
      ? [meta.chart_data]
      : [];
  const images = meta?.images ?? [];
  const chartCount = Math.max(chartDataList.length, images.length);
  for (let i = 0; i < chartCount; i += 1) {
    charts.push({
      url: images[i] ?? "",
      chartData: chartDataList[i] ?? null,
      title: chartDataList[i]?.title,
    });
  }

  return {
    role: message.role === "user" ? "user" : "assistant",
    content: message.content,
    live: false,
    steps,
    sql: meta?.sql && meta.sql !== "-" ? meta.sql : "",
    charts,
    references: meta?.references ?? [],
    needsClarification: Boolean(meta?.needs_clarification),
    clarifyOptions: meta?.clarify_options ?? [],
    error: "",
    messageIndex,
  };
}

export function viewFromStreaming(state: {
  question: string;
  steps: LiveStep[];
  content: string;
  charts: LiveChart[];
  clarify: { question: string; options: string[] } | null;
  error: string;
}): { user: MessageView; assistant: MessageView } {
  const lastSqlStep = [...state.steps].reverse().find((step) => step.sql);
  return {
    user: {
      role: "user",
      content: state.question,
      live: false,
      steps: [],
      sql: "",
      charts: [],
      references: [],
      needsClarification: false,
      clarifyOptions: [],
      error: "",
      messageIndex: -1,
    },
    assistant: {
      role: "assistant",
      content: state.clarify ? state.clarify.question : state.content,
      live: true,
      steps: state.steps,
      sql: lastSqlStep?.sql ?? "",
      rowCount: undefined,
      rows: lastSqlStep?.rows,
      columns: lastSqlStep?.columns,
      charts: state.charts,
      references: state.steps.flatMap((step) => step.items ?? []),
      needsClarification: Boolean(state.clarify),
      clarifyOptions: state.clarify?.options ?? [],
      error: state.error,
      messageIndex: -1,
    },
  };
}
