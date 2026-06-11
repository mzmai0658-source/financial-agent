import { defineStore } from "pinia";

import { createSession, deleteSession, getSession, queryChat, streamChat } from "@/services/api";
import type {
  ChartData,
  ChatQueryResponse,
  ExecutionStep,
  ReferenceItem,
  SessionMessage,
  SessionResponse,
} from "@/types/api";

const STORAGE_KEY = "financial-chat-agent-session";

export interface LiveStep {
  tool: string;
  label: string;
  detail: string;
  status: "running" | "done" | "error" | "empty" | "rejected";
  summary?: string;
  sql?: string;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  items?: Array<Partial<ReferenceItem>>;
}

export interface LiveChart {
  url: string;
  chartData?: ChartData | null;
  title?: string;
}

interface StreamingState {
  active: boolean;
  question: string;
  steps: LiveStep[];
  content: string;
  charts: LiveChart[];
  clarify: { question: string; options: string[] } | null;
  error: string;
}

interface SessionState {
  sessionId: string;
  messages: SessionMessage[];
  latestContext: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  hydrated: boolean;
  streaming: StreamingState;
}

function emptyStreaming(): StreamingState {
  return {
    active: false,
    question: "",
    steps: [],
    content: "",
    charts: [],
    clarify: null,
    error: "",
  };
}

function initialState(): SessionState {
  return {
    sessionId: "",
    messages: [],
    latestContext: {},
    createdAt: "",
    updatedAt: "",
    hydrated: false,
    streaming: emptyStreaming(),
  };
}

let abortController: AbortController | null = null;

export const useSessionStore = defineStore("session", {
  state: (): SessionState => initialState(),
  getters: {
    assistantMessages: (state) => state.messages.filter((message) => message.role === "assistant"),
    latestAssistantMessage: (state) =>
      [...state.messages].reverse().find((message) => message.role === "assistant") ?? null,
    isStreaming: (state) => state.streaming.active,
  },
  actions: {
    hydrate() {
      if (this.hydrated) return;
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as Partial<SessionState>;
          this.sessionId = parsed.sessionId ?? "";
          this.messages = parsed.messages ?? [];
          this.latestContext = parsed.latestContext ?? {};
          this.createdAt = parsed.createdAt ?? "";
          this.updatedAt = parsed.updatedAt ?? "";
        } catch {
          window.localStorage.removeItem(STORAGE_KEY);
        }
      }
      this.hydrated = true;
    },
    persist() {
      try {
        window.localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({
            sessionId: this.sessionId,
            messages: this.messages,
            latestContext: this.latestContext,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt,
          }),
        );
      } catch {
        // localStorage 配额满时放弃本地缓存；会话以后端存储为准
      }
    },
    syncFromSession(response: SessionResponse) {
      this.sessionId = response.session_id;
      this.messages = response.messages;
      this.latestContext = response.latest_context ?? {};
      this.createdAt = response.created_at;
      this.updatedAt = response.updated_at;
      this.persist();
    },
    syncFromQuery(response: ChatQueryResponse) {
      this.sessionId = response.session_id;
      this.messages = response.messages;
      this.latestContext = response.context ?? {};
      this.updatedAt = new Date().toISOString();
      if (!this.createdAt) {
        this.createdAt = this.updatedAt;
      }
      this.persist();
    },
    resetLocalState() {
      Object.assign(this, initialState());
    },
    async ensureSession() {
      this.hydrate();
      if (!this.sessionId) {
        const created = await createSession();
        this.syncFromSession(created);
        return;
      }
      try {
        const session = await getSession(this.sessionId);
        this.syncFromSession(session);
      } catch {
        this.resetLocalState();
        this.hydrated = true;
        const created = await createSession();
        this.syncFromSession(created);
      }
    },
    async startFresh() {
      this.stopStreaming();
      if (this.sessionId) {
        try {
          await deleteSession(this.sessionId);
        } catch {
          // 失效的本地会话直接丢弃即可，后端会新建
        }
      }
      this.resetLocalState();
      this.hydrated = true;
      const created = await createSession();
      this.syncFromSession(created);
    },
    stopStreaming() {
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      if (this.streaming.active) {
        this.finalizeInterruptedStream();
      }
    },
    finalizeInterruptedStream(placeholder = "（已停止生成）") {
      // 把已生成的内容固化为一条消息，避免中断后丢失
      const now = new Date().toISOString();
      this.messages = [
        ...this.messages,
        { role: "user", content: this.streaming.question, ts: now },
        {
          role: "assistant",
          content: this.streaming.content || placeholder,
          ts: now,
          metadata: null,
        },
      ];
      this.streaming = emptyStreaming();
      this.persist();
    },
    async sendQuestion(question: string) {
      this.hydrate();
      if (this.streaming.active) return;

      this.streaming = {
        ...emptyStreaming(),
        active: true,
        question,
      };
      const controller = new AbortController();
      abortController = controller;
      // 流是否已建立：建立后后端无论如何都会把本轮跑完并落库，
      // 此时绝不能再走非流式降级（会把同一问题重复执行一遍）。
      let streamStarted = false;

      const markRunningDone = () => {
        for (const step of this.streaming.steps) {
          if (step.status === "running") step.status = "done";
        }
      };

      try {
        await streamChat(
          { sessionId: this.sessionId || undefined, question },
          {
            onSession: (event) => {
              streamStarted = true;
              if (!this.sessionId) this.sessionId = event.session_id;
            },
            onPlan: (event) => {
              markRunningDone();
              this.streaming.steps.push({
                tool: "plan",
                label: event.label,
                detail: event.detail ?? "",
                status: "running",
              });
            },
            onToolCall: (event) => {
              markRunningDone();
              this.streaming.steps.push({
                tool: event.tool,
                label: event.label,
                detail: event.detail ?? "",
                status: "running",
              });
            },
            onToolResult: (event) => {
              const step = [...this.streaming.steps]
                .reverse()
                .find((item) => item.tool === event.tool && item.status === "running");
              const status =
                event.status === "success" || event.status === "done"
                  ? "done"
                  : event.status === "empty"
                    ? "empty"
                    : event.status === "rejected"
                      ? "rejected"
                      : "error";
              if (step) {
                step.status = status as LiveStep["status"];
                step.summary = event.summary ?? "";
                step.sql = event.sql;
                step.rows = event.rows;
                step.columns = event.columns;
                step.items = event.items;
              }
            },
            onAnswerDelta: (event) => {
              markRunningDone();
              this.streaming.content += event.text;
            },
            onChart: (event) => {
              if (event.url || event.path) {
                this.streaming.charts.push({
                  url: event.url ?? event.path ?? "",
                  chartData: event.chart_data,
                  title: event.title,
                });
              }
            },
            onClarify: (event) => {
              markRunningDone();
              this.streaming.clarify = {
                question: event.question,
                options: event.options ?? [],
              };
            },
            onError: (event) => {
              this.streaming.error = event.message;
            },
            onDone: (response) => {
              this.syncFromQuery(response);
            },
          },
          controller.signal,
        );
        this.streaming = emptyStreaming();
      } catch (error) {
        if (controller.signal.aborted) {
          // 用户主动停止：stopStreaming 已固化消息，这里直接退出
          return;
        }
        if (streamStarted) {
          // 流中途断开：后端会继续完成并落库，固化已收到的内容即可，不能重发
          this.finalizeInterruptedStream("（连接中断，结果已在后台继续生成，稍后刷新可查看）");
          return;
        }
        // 流式接口从未建立时才降级到非流式接口
        try {
          const response = await queryChat({ sessionId: this.sessionId || undefined, question });
          this.syncFromQuery(response);
          this.streaming = emptyStreaming();
        } catch {
          this.streaming.error = "请求失败，请检查后端服务是否启动。";
          this.streaming.active = false;
          throw error;
        }
      } finally {
        if (abortController === controller) {
          abortController = null;
        }
      }
    },
  },
});
