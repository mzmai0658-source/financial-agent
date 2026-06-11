<script setup lang="ts">
defineProps<{
  variant?: "primary" | "ghost" | "outline" | "danger";
  size?: "sm" | "md";
  disabled?: boolean;
  block?: boolean;
}>();
</script>

<template>
  <button
    class="btn"
    :class="[`btn--${variant ?? 'outline'}`, `btn--${size ?? 'md'}`, { 'btn--block': block }]"
    :disabled="disabled"
    type="button"
  >
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: var(--r-sm);
  border: 1px solid transparent;
  font-size: var(--fs-md);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast);
  white-space: nowrap;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn--md {
  height: 36px;
  padding: 0 var(--sp-4);
}

.btn--sm {
  height: 28px;
  padding: 0 var(--sp-3);
  font-size: var(--fs-sm);
}

.btn--block {
  width: 100%;
}

.btn--primary {
  background: var(--c-primary);
  color: #fff;
}

.btn--primary:hover:not(:disabled) {
  background: var(--c-primary-hover);
}

.btn--primary:active:not(:disabled) {
  background: var(--c-primary-active);
}

.btn--outline {
  background: var(--c-surface);
  border-color: var(--c-border-strong);
  color: var(--c-text);
}

.btn--outline:hover:not(:disabled) {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

.btn--ghost {
  background: transparent;
  color: var(--c-text-secondary);
}

.btn--ghost:hover:not(:disabled) {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.btn--danger {
  background: var(--c-surface);
  border-color: var(--c-border-strong);
  color: var(--c-danger);
}

.btn--danger:hover:not(:disabled) {
  border-color: var(--c-danger);
  background: var(--c-danger-soft);
}
</style>
