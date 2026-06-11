<script setup lang="ts">
defineProps<{
  options: string[];
  disabled?: boolean;
}>();

defineEmits<{ select: [option: string] }>();
</script>

<template>
  <div v-if="options.length" class="clarify">
    <button
      v-for="option in options"
      :key="option"
      class="clarify__option"
      type="button"
      :disabled="disabled"
      @click="$emit('select', option)"
    >
      {{ option }}
    </button>
  </div>
</template>

<style scoped>
.clarify {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.clarify__option {
  height: 30px;
  padding: 0 var(--sp-3);
  border-radius: var(--r-full);
  border: 1px solid var(--c-primary-border);
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: all var(--t-fast);
}

.clarify__option:hover:not(:disabled) {
  background: var(--c-primary);
  color: #fff;
}

.clarify__option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
