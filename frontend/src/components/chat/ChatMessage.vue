<script setup lang="ts">
import type { MessageView } from "./messageView";
import { renderMarkdown } from "@/utils/markdown";
import ThinkingTrail from "./ThinkingTrail.vue";
import SqlCard from "./SqlCard.vue";
import ChartCard from "./ChartCard.vue";
import ReferenceCards from "./ReferenceCards.vue";
import ClarifyOptions from "./ClarifyOptions.vue";
import { IconAlert, IconSpark } from "@/components/ui/icons";
import type { EvidenceTab } from "@/stores/ui";

defineProps<{
  view: MessageView;
  clarifyDisabled?: boolean;
}>();

defineEmits<{
  openDetail: [tab: EvidenceTab, messageIndex: number];
  selectClarify: [option: string];
}>();
</script>

<template>
  <article class="msg" :class="`msg--${view.role}`">
    <div v-if="view.role === 'assistant'" class="msg__avatar">
      <IconSpark :size="15" />
    </div>

    <div class="msg__main">
      <!-- 用户消息 -->
      <div v-if="view.role === 'user'" class="msg__bubble msg__bubble--user">{{ view.content }}</div>

      <!-- 助手消息：执行轨迹 + 证据卡片 + 正文 -->
      <div v-else class="msg__stack">
        <ThinkingTrail v-if="view.steps.length" :steps="view.steps" :live="view.live" />

        <SqlCard
          v-if="view.sql"
          :sql="view.sql"
          :row-count="view.rowCount"
          :rows="view.rows"
          :columns="view.columns"
          @open-detail="$emit('openDetail', 'sql', view.messageIndex)"
        />

        <div v-if="view.content" class="msg__bubble msg__bubble--assistant" :class="{ 'msg__bubble--clarify': view.needsClarification }">
          <!-- eslint-disable-next-line vue/no-v-html — 内容已在 renderMarkdown 内转义原始 HTML -->
          <div class="msg__content" v-html="renderMarkdown(view.content)" /><span v-if="view.live && !view.needsClarification" class="msg__cursor" />
        </div>

        <ClarifyOptions
          v-if="view.needsClarification && view.clarifyOptions.length"
          :options="view.clarifyOptions"
          :disabled="clarifyDisabled"
          @select="$emit('selectClarify', $event)"
        />

        <ChartCard
          v-for="(chart, idx) in view.charts"
          :key="idx"
          :chart-data="chart.chartData"
          :image-url="chart.url"
          :title="chart.title"
        />

        <ReferenceCards
          v-if="view.references.length"
          :references="view.references"
          compact
          @open-detail="$emit('openDetail', 'refs', view.messageIndex)"
        />

        <div v-if="view.error" class="msg__error">
          <IconAlert :size="14" />
          {{ view.error }}
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.msg {
  display: flex;
  gap: var(--sp-3);
  animation: fade-in-up 0.25s ease;
}

.msg--user {
  justify-content: flex-end;
}

.msg__avatar {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}

.msg__main {
  min-width: 0;
  max-width: 92%;
}

.msg--assistant .msg__main {
  flex: 1;
}

.msg__stack {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.msg__bubble {
  border-radius: var(--r-lg);
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fs-md);
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.msg__bubble--user {
  background: var(--c-primary);
  color: #fff;
  border-bottom-right-radius: var(--r-sm);
  white-space: pre-wrap;
}

.msg__bubble--assistant {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-top-left-radius: var(--r-sm);
}

.msg__bubble--clarify {
  border-color: var(--c-warning);
  background: var(--c-warning-soft);
}

.msg__content {
  display: inline;
}

/* Markdown 渲染样式 */
.msg__content :deep(p) {
  margin: 0 0 var(--sp-2);
}

.msg__content :deep(p:last-child) {
  margin-bottom: 0;
}

.msg__content :deep(ul),
.msg__content :deep(ol) {
  margin: var(--sp-1) 0 var(--sp-2);
  padding-left: 22px;
}

.msg__content :deep(li) {
  margin-bottom: 2px;
}

.msg__content :deep(h1),
.msg__content :deep(h2),
.msg__content :deep(h3),
.msg__content :deep(h4) {
  font-size: var(--fs-md);
  font-weight: 600;
  margin: var(--sp-3) 0 var(--sp-2);
}

.msg__content :deep(code) {
  font-size: var(--fs-xs);
  background: var(--c-code-bg);
  border-radius: 4px;
  padding: 1px 5px;
}

.msg__content :deep(pre) {
  background: var(--c-code-bg);
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
  overflow-x: auto;
  margin: var(--sp-2) 0;
}

.msg__content :deep(pre code) {
  background: none;
  padding: 0;
}

.msg__content :deep(table) {
  border-collapse: collapse;
  margin: var(--sp-2) 0;
  font-size: var(--fs-sm);
  width: 100%;
}

.msg__content :deep(th),
.msg__content :deep(td) {
  border: 1px solid var(--c-border);
  padding: 5px 10px;
  text-align: left;
}

.msg__content :deep(th) {
  background: var(--c-surface-muted);
  font-weight: 500;
}

.msg__content :deep(blockquote) {
  margin: var(--sp-2) 0;
  padding: var(--sp-1) var(--sp-3);
  border-left: 3px solid var(--c-primary-border);
  color: var(--c-text-secondary);
}

.msg__content :deep(hr) {
  border: none;
  border-top: 1px solid var(--c-border);
  margin: var(--sp-3) 0;
}

.msg__cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--c-primary);
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 0.9s step-end infinite;
}

.msg__error {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}
</style>
