<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ChatInput from "@/components/chat/ChatInput.vue";
import ChatMessage from "@/components/chat/ChatMessage.vue";
import ContextChips from "@/components/chat/ContextChips.vue";
import { viewFromMessage, viewFromStreaming, type MessageView } from "@/components/chat/messageView";
import EvidenceRail from "@/components/EvidenceRail.vue";
import StatusDots from "@/components/StatusDots.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import { IconPlus, IconSpark } from "@/components/ui/icons";
import { fetchHealth } from "@/services/api";
import { useExamplesStore } from "@/stores/examples";
import { useSessionStore } from "@/stores/session";
import { useUiStore, type EvidenceTab } from "@/stores/ui";

const route = useRoute();
const router = useRouter();
const sessionStore = useSessionStore();
const examplesStore = useExamplesStore();
const ui = useUiStore();

const scrollEl = ref<HTMLDivElement | null>(null);
const autoFollow = ref(true);

const messageViews = computed<MessageView[]>(() =>
  sessionStore.messages.map((message, index) => viewFromMessage(message, index)),
);

const streamingViews = computed(() =>
  sessionStore.streaming.active ? viewFromStreaming(sessionStore.streaming) : null,
);

const hasConversation = computed(
  () => messageViews.value.length > 0 || sessionStore.streaming.active,
);

/** 证据栏聚焦的消息视图：流式优先，其次用户选中的，最后取最新一条助手消息 */
const focusedView = computed<MessageView | null>(() => {
  if (streamingViews.value) return streamingViews.value.assistant;
  if (ui.focusedMessageIndex >= 0 && ui.focusedMessageIndex < messageViews.value.length) {
    const view = messageViews.value[ui.focusedMessageIndex];
    if (view?.role === "assistant") return view;
  }
  for (let i = messageViews.value.length - 1; i >= 0; i -= 1) {
    if (messageViews.value[i].role === "assistant") return messageViews.value[i];
  }
  return null;
});

onMounted(async () => {
  examplesStore.ensureLoaded();
  fetchHealth()
    .then((health) => ui.setHealth(health))
    .catch(() => ui.setHealth(null));

  await sessionStore.ensureSession().catch(() => {
    ui.setError("无法连接后端服务，请确认 API 已启动（默认 :8000）。");
  });

  const presetQuestion = String(route.query.q ?? "").trim();
  if (presetQuestion) {
    router.replace({ query: {} });
    send(presetQuestion);
  }
  scrollToBottom(true);
});

function onScroll() {
  const el = scrollEl.value;
  if (!el) return;
  autoFollow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
}

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = scrollEl.value;
    if (!el) return;
    if (force || autoFollow.value) {
      el.scrollTop = el.scrollHeight;
    }
  });
}

watch(
  () => [sessionStore.streaming.content, sessionStore.streaming.steps.length, sessionStore.messages.length],
  () => scrollToBottom(),
);

async function send(question: string) {
  ui.setError("");
  ui.focusMessage(-1);
  autoFollow.value = true;
  scrollToBottom(true);
  try {
    await sessionStore.sendQuestion(question);
  } catch {
    ui.setError("请求失败，请检查后端服务是否启动。");
  }
}

function stop() {
  sessionStore.stopStreaming();
}

async function newConversation() {
  ui.setError("");
  ui.focusMessage(-1);
  try {
    await sessionStore.startFresh();
  } catch {
    ui.setError("新建会话失败，请检查后端服务。");
  }
}

function openDetail(tab: EvidenceTab, messageIndex: number) {
  ui.openRail(tab, messageIndex);
}
</script>

<template>
  <div class="ws">
    <!-- 左侧导航 -->
    <aside class="ws__left">
      <router-link class="ws__brand" to="/">
        <span class="ws__brand-mark"><IconSpark :size="15" /></span>
        财报智能体
      </router-link>

      <BaseButton variant="primary" block @click="newConversation">
        <IconPlus :size="15" />
        新对话
      </BaseButton>

      <div class="ws__section">
        <h3 class="ws__section-title">试试这样问</h3>
        <button
          v-for="example in examplesStore.examples"
          :key="example.id"
          class="ws__example"
          type="button"
          :disabled="sessionStore.isStreaming"
          @click="send(example.question)"
        >
          {{ example.question }}
        </button>
      </div>

      <footer class="ws__left-footer">
        <StatusDots :health="ui.health" />
      </footer>
    </aside>

    <!-- 中央对话区 -->
    <main class="ws__center">
      <div ref="scrollEl" class="ws__scroll" @scroll.passive="onScroll">
        <div class="ws__thread">
          <!-- 空状态 -->
          <div v-if="!hasConversation" class="ws__welcome">
            <div class="ws__welcome-mark"><IconSpark :size="22" /></div>
            <h2>开始你的财报分析</h2>
            <p>查数字、看趋势、比公司、问原因，缺少条件时我会先和你确认。</p>
          </div>

          <!-- 历史消息 -->
          <ChatMessage
            v-for="view in messageViews"
            :key="`${view.messageIndex}-${view.role}`"
            :view="view"
            :clarify-disabled="sessionStore.isStreaming || view.messageIndex !== messageViews.length - 1"
            @open-detail="openDetail"
            @select-clarify="send"
          />

          <!-- 流式进行中 -->
          <template v-if="streamingViews">
            <ChatMessage :view="streamingViews.user" />
            <ChatMessage
              :view="streamingViews.assistant"
              :clarify-disabled="false"
              @open-detail="(tab) => ui.openRail(tab)"
              @select-clarify="send"
            />
          </template>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="ws__composer">
        <div v-if="ui.error" class="ws__error">{{ ui.error }}</div>
        <ContextChips :context="sessionStore.latestContext" />
        <ChatInput :streaming="sessionStore.isStreaming" @send="send" @stop="stop" />
        <p class="ws__hint">Enter 发送 · Shift+Enter 换行 · 回答基于财报数据库与研报知识库生成</p>
      </div>
    </main>

    <!-- 右侧证据栏（常驻可展开） -->
    <EvidenceRail :view="focusedView" />
  </div>
</template>

<style scoped>
.ws {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--c-bg);
}

/* 左侧 */
.ws__left {
  flex-shrink: 0;
  width: var(--rail-left-w);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  padding: var(--sp-4);
  border-right: 1px solid var(--c-border);
  background: var(--c-surface);
  overflow-y: auto;
}

.ws__brand {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-weight: 600;
  color: var(--c-text);
}

.ws__brand:hover {
  text-decoration: none;
}

.ws__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
}

.ws__section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-height: 0;
}

.ws__section-title {
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--c-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.ws__example {
  text-align: left;
  border: 1px solid transparent;
  background: none;
  border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: all var(--t-fast);
}

.ws__example:hover:not(:disabled) {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.ws__example:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ws__left-footer {
  border-top: 1px solid var(--c-border);
  padding-top: var(--sp-3);
}

/* 中央 */
.ws__center {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ws__scroll {
  flex: 1;
  overflow-y: auto;
}

.ws__thread {
  max-width: var(--chat-max-w);
  margin: 0 auto;
  padding: var(--sp-6) var(--sp-5) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.ws__welcome {
  text-align: center;
  padding: var(--sp-12) var(--sp-4);
  color: var(--c-text-secondary);
}

.ws__welcome-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: var(--r-lg);
  background: var(--c-primary-soft);
  color: var(--c-primary);
  margin-bottom: var(--sp-4);
}

.ws__welcome h2 {
  font-size: var(--fs-xl);
  color: var(--c-text);
}

.ws__welcome p {
  margin-top: var(--sp-2);
  font-size: var(--fs-sm);
}

/* 输入区 */
.ws__composer {
  max-width: var(--chat-max-w);
  width: 100%;
  margin: 0 auto;
  padding: var(--sp-2) var(--sp-5) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.ws__error {
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}

.ws__hint {
  text-align: center;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

@media (max-width: 960px) {
  .ws__left {
    display: none;
  }
}
</style>
