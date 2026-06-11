<script setup lang="ts">
import { computed, ref } from "vue";

import type { LiveStep } from "@/stores/session";
import BaseSpinner from "@/components/ui/BaseSpinner.vue";
import { IconCheck, IconAlert, IconChevronDown, IconChevronRight } from "@/components/ui/icons";

const props = defineProps<{
  steps: LiveStep[];
  live?: boolean;
}>();

const collapsed = ref(!props.live);

const runningStep = computed(() => props.steps.find((step) => step.status === "running"));
const doneCount = computed(() => props.steps.filter((step) => step.status !== "running").length);

function statusTone(status: LiveStep["status"]): string {
  if (status === "done") return "ok";
  if (status === "running") return "running";
  if (status === "empty") return "warn";
  return "error";
}
</script>

<template>
  <div class="trail" :class="{ 'trail--live': live }">
    <button class="trail__head" type="button" @click="collapsed = !collapsed">
      <component :is="collapsed ? IconChevronRight : IconChevronDown" :size="14" />
      <template v-if="live && runningStep">
        <BaseSpinner :size="13" />
        <span class="trail__head-label">{{ runningStep.label }}…</span>
      </template>
      <template v-else>
        <span class="trail__head-label">执行过程（{{ doneCount }} 步）</span>
      </template>
    </button>

    <ol v-if="!collapsed" class="trail__list">
      <li v-for="(step, idx) in steps" :key="idx" class="trail__item">
        <span class="trail__status" :class="`trail__status--${statusTone(step.status)}`">
          <BaseSpinner v-if="step.status === 'running'" :size="12" />
          <IconCheck v-else-if="step.status === 'done'" :size="12" />
          <IconAlert v-else :size="12" />
        </span>
        <div class="trail__body">
          <span class="trail__label">{{ step.label }}</span>
          <span v-if="step.summary" class="trail__summary">{{ step.summary }}</span>
          <code v-if="step.detail && step.tool === 'query_database'" class="trail__detail">{{ step.detail }}</code>
          <span v-else-if="step.detail" class="trail__detail-text">{{ step.detail }}</span>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.trail {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
  font-size: var(--fs-sm);
  overflow: hidden;
}

.trail--live {
  border-color: var(--c-primary-border);
}

.trail__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  border: none;
  background: none;
  padding: var(--sp-2) var(--sp-3);
  cursor: pointer;
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
}

.trail__head:hover {
  color: var(--c-text);
}

.trail__head-label {
  font-weight: 500;
}

.trail__list {
  margin: 0;
  padding: 0 var(--sp-3) var(--sp-3);
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.trail__item {
  display: flex;
  gap: var(--sp-2);
  align-items: flex-start;
}

.trail__status {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  margin-top: 1px;
}

.trail__status--ok {
  color: var(--c-success);
  background: var(--c-success-soft);
}

.trail__status--running {
  color: var(--c-primary);
}

.trail__status--warn {
  color: var(--c-warning);
  background: var(--c-warning-soft);
}

.trail__status--error {
  color: var(--c-danger);
  background: var(--c-danger-soft);
}

.trail__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.trail__label {
  font-weight: 500;
  color: var(--c-text);
}

.trail__summary {
  color: var(--c-text-secondary);
  font-size: var(--fs-xs);
}

.trail__detail {
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
  background: var(--c-code-bg);
  border-radius: var(--r-sm);
  padding: 2px 6px;
  overflow-wrap: anywhere;
  display: block;
}

.trail__detail-text {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  overflow-wrap: anywhere;
}
</style>
