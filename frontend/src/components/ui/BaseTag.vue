<script setup lang="ts">
defineProps<{
  tone?: "default" | "primary" | "success" | "warning" | "danger";
  closable?: boolean;
}>();

defineEmits<{ close: [] }>();
</script>

<template>
  <span class="tag" :class="`tag--${tone ?? 'default'}`">
    <slot />
    <button v-if="closable" class="tag__close" type="button" aria-label="移除" @click.stop="$emit('close')">×</button>
  </span>
</template>

<style scoped>
.tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-full);
  font-size: var(--fs-xs);
  line-height: 1;
  border: 1px solid var(--c-border);
  background: var(--c-surface-muted);
  color: var(--c-text-secondary);
  white-space: nowrap;
}

.tag--primary {
  background: var(--c-primary-soft);
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

.tag--success {
  background: var(--c-success-soft);
  border-color: transparent;
  color: var(--c-success);
}

.tag--warning {
  background: var(--c-warning-soft);
  border-color: transparent;
  color: var(--c-warning);
}

.tag--danger {
  background: var(--c-danger-soft);
  border-color: transparent;
  color: var(--c-danger);
}

.tag__close {
  border: none;
  background: none;
  padding: 0;
  margin-left: 2px;
  cursor: pointer;
  color: inherit;
  font-size: 14px;
  line-height: 1;
  opacity: 0.7;
}

.tag__close:hover {
  opacity: 1;
}
</style>
