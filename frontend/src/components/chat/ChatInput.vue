<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import { IconArrowUp, IconStop } from "@/components/ui/icons";

const props = defineProps<{
  streaming?: boolean;
  placeholder?: string;
  modelValue?: string;
}>();

const emit = defineEmits<{
  send: [question: string];
  stop: [];
  "update:modelValue": [value: string];
}>();

const text = ref(props.modelValue ?? "");
const textareaEl = ref<HTMLTextAreaElement | null>(null);

watch(
  () => props.modelValue,
  (value) => {
    if (value !== undefined && value !== text.value) {
      text.value = value;
      nextTick(autoResize);
    }
  },
);

function autoResize() {
  const el = textareaEl.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

function onInput() {
  emit("update:modelValue", text.value);
  autoResize();
}

function submit() {
  const question = text.value.trim();
  if (!question || props.streaming) return;
  emit("send", question);
  text.value = "";
  emit("update:modelValue", "");
  nextTick(autoResize);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
}

defineExpose({ focus: () => textareaEl.value?.focus() });
</script>

<template>
  <div class="chat-input">
    <textarea
      ref="textareaEl"
      v-model="text"
      class="chat-input__textarea"
      :placeholder="placeholder ?? '像聊天一样提问，例如：药明康德 2024 年净利润是多少？'"
      rows="1"
      maxlength="900"
      @input="onInput"
      @keydown="onKeydown"
    />
    <button
      v-if="streaming"
      class="chat-input__btn chat-input__btn--stop"
      type="button"
      title="停止生成"
      @click="$emit('stop')"
    >
      <IconStop :size="16" />
    </button>
    <button
      v-else
      class="chat-input__btn"
      type="button"
      title="发送（Enter）"
      :disabled="!text.trim()"
      @click="submit"
    >
      <IconArrowUp :size="16" />
    </button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  align-items: flex-end;
  gap: var(--sp-2);
  border: 1px solid var(--c-border-strong);
  border-radius: var(--r-lg);
  background: var(--c-surface);
  padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-4);
  box-shadow: var(--shadow-md);
  transition: border-color var(--t-fast);
}

.chat-input:focus-within {
  border-color: var(--c-primary);
}

.chat-input__textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: var(--font-base);
  font-size: var(--fs-md);
  line-height: 1.6;
  color: var(--c-text);
  padding: 6px 0;
  max-height: 160px;
}

.chat-input__textarea::placeholder {
  color: var(--c-text-tertiary);
}

.chat-input__btn {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background var(--t-fast);
}

.chat-input__btn:hover:not(:disabled) {
  background: var(--c-primary-hover);
}

.chat-input__btn:disabled {
  background: var(--c-border-strong);
  cursor: not-allowed;
}

.chat-input__btn--stop {
  background: var(--c-danger);
}

.chat-input__btn--stop:hover {
  background: #a73631;
}
</style>
