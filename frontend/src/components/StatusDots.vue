<script setup lang="ts">
import { computed } from "vue";

import type { HealthResponse } from "@/types/api";

const props = defineProps<{ health: HealthResponse | null }>();

const items = computed(() => [
  { key: "service", label: "API", ok: props.health?.service?.ok ?? false },
  { key: "database", label: "数据库", ok: props.health?.database?.ok ?? false },
  { key: "knowledge_base", label: "知识库", ok: props.health?.knowledge_base?.ok ?? false },
  { key: "llm", label: "模型", ok: props.health?.llm?.ok ?? false },
]);
</script>

<template>
  <div class="status-dots">
    <span v-for="item in items" :key="item.key" class="status-dots__item" :title="item.label">
      <span class="status-dots__dot" :class="{ 'status-dots__dot--ok': item.ok }" />
      {{ item.label }}
    </span>
  </div>
</template>

<style scoped>
.status-dots {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.status-dots__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.status-dots__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--c-danger);
}

.status-dots__dot--ok {
  background: var(--c-success);
}
</style>
