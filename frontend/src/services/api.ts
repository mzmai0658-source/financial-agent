import axios from "axios";

import type {
  ChatQueryResponse,
  ExamplesResponse,
  HealthResponse,
  SessionResponse,
  StreamHandlers,
} from "@/types/api";

function resolveApiBaseUrl(): string {
  const envBase = String(import.meta.env.VITE_API_BASE_URL ?? "").trim();
  if (envBase) {
    return envBase.replace(/\/$/, "");
  }

  if (typeof window === "undefined") {
    return "";
  }

  const { hostname, port, protocol } = window.location;
  const isLocalHost = hostname === "localhost" || hostname === "127.0.0.1";
  if (!isLocalHost) {
    return "";
  }

  if (port === "5173") {
    return "";
  }
  return `${protocol}//${hostname}:8000`;
}

const API_BASE_URL = resolveApiBaseUrl();

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
});

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>("/api/health");
  return data;
}

export async function fetchExamples(): Promise<ExamplesResponse> {
  const { data } = await api.get<ExamplesResponse>("/api/examples");
  return data;
}

export async function createSession(): Promise<SessionResponse> {
  const { data } = await api.post<SessionResponse>("/api/sessions", {});
  return data;
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  const { data } = await api.get<SessionResponse>(`/api/sessions/${sessionId}`);
  return data;
}

export async function deleteSession(sessionId: string): Promise<void> {
  await api.delete(`/api/sessions/${sessionId}`);
}

export async function queryChat(payload: {
  sessionId?: string;
  question: string;
}): Promise<ChatQueryResponse> {
  const body: Record<string, string> = { question: payload.question };
  if (payload.sessionId) {
    body.session_id = payload.sessionId;
  }
  const { data } = await api.post<ChatQueryResponse>("/api/chat/query", body);
  return data;
}

/**
 * SSE 流式问答：通过 fetch + ReadableStream 解析 text/event-stream。
 * 返回 abort 函数，可中途停止。
 */
export async function streamChat(
  payload: { sessionId?: string; question: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const body: Record<string, string> = { question: payload.question };
  if (payload.sessionId) {
    body.session_id = payload.sessionId;
  }

  const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream request failed: HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const dispatch = (eventType: string, dataText: string) => {
    let data: unknown;
    try {
      data = JSON.parse(dataText);
    } catch {
      return;
    }
    switch (eventType) {
      case "session":
        handlers.onSession?.(data as never);
        break;
      case "plan":
        handlers.onPlan?.(data as never);
        break;
      case "tool_call":
        handlers.onToolCall?.(data as never);
        break;
      case "tool_result":
        handlers.onToolResult?.(data as never);
        break;
      case "answer_delta":
        handlers.onAnswerDelta?.(data as never);
        break;
      case "chart":
        handlers.onChart?.(data as never);
        break;
      case "clarify":
        handlers.onClarify?.(data as never);
        break;
      case "error":
        handlers.onError?.(data as never);
        break;
      case "done":
        handlers.onDone?.(data as never);
        break;
      default:
        break;
    }
  };

  const processBuffer = () => {
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const block = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      let eventType = "";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) {
          eventType = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).trim());
        }
      }
      if (eventType && dataLines.length) {
        dispatch(eventType, dataLines.join("\n"));
      }
      separatorIndex = buffer.indexOf("\n\n");
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    processBuffer();
  }
  buffer += decoder.decode();
  processBuffer();
}

export function assetUrl(raw?: string | null): string {
  if (!raw) return "";
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    return raw;
  }
  const base = API_BASE_URL.replace(/\/api$/, "");
  return base ? `${base}${raw}` : raw;
}
