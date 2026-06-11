import { marked } from "marked";

marked.setOptions({
  gfm: true,
  breaks: true,
});

/** 转义原始 HTML，防止 LLM 输出注入标签；随后再交给 marked 解析 Markdown。 */
function escapeHtml(raw: string): string {
  return raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function renderMarkdown(content: string): string {
  const escaped = escapeHtml(String(content ?? ""));
  return marked.parse(escaped, { async: false }) as string;
}
