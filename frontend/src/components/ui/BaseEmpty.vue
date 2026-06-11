<script setup lang="ts">
defineProps<{ text?: string }>();
</script>

<template>
  <div class="empty">
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 9h18" />
      <path d="M8 14h4" />
    </svg>
    <p>{{ text ?? "暂无内容" }}</p>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-8) var(--sp-4);
  color: var(--c-text-tertiary);
  font-size: var(--fs-sm);
}
</style>
