<script setup lang="ts">
import { computed } from "vue";

import BaseTag from "@/components/ui/BaseTag.vue";

const props = defineProps<{
  context: Record<string, unknown>;
}>();

const chips = computed(() => {
  const list: Array<{ key: string; label: string }> = [];
  const ctx = props.context ?? {};
  if (ctx.company) list.push({ key: "company", label: `公司：${ctx.company}` });
  if (ctx.report_year) list.push({ key: "report_year", label: `年份：${ctx.report_year}` });
  if (ctx.report_period) list.push({ key: "report_period", label: `报告期：${ctx.report_period}` });
  if (ctx.metric_keyword) list.push({ key: "metric_keyword", label: `指标：${ctx.metric_keyword}` });
  return list;
});
</script>

<template>
  <div v-if="chips.length" class="context-chips">
    <span class="context-chips__label">当前上下文</span>
    <BaseTag v-for="chip in chips" :key="chip.key" tone="primary">
      {{ chip.label }}
    </BaseTag>
  </div>
</template>

<style scoped>
.context-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.context-chips__label {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}
</style>
