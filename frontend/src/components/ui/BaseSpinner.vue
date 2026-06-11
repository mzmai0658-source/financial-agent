<script setup lang="ts">
defineProps<{ size?: number }>();
</script>

<template>
  <span
    class="spinner"
    :style="{ width: `${size ?? 16}px`, height: `${size ?? 16}px` }"
    role="status"
    aria-label="加载中"
  />
</template>

<style scoped>
.spinner {
  display: inline-block;
  border: 2px solid var(--c-border-strong);
  border-top-color: var(--c-primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
</style>
