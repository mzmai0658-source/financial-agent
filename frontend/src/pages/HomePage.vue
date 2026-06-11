<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import ChatInput from "@/components/chat/ChatInput.vue";
import { IconSpark } from "@/components/ui/icons";
import { useExamplesStore } from "@/stores/examples";

const router = useRouter();
const examplesStore = useExamplesStore();
const draft = ref("");

onMounted(() => {
  examplesStore.ensureLoaded();
});

function startChat(question: string) {
  router.push({ path: "/workspace", query: { q: question } });
}
</script>

<template>
  <div class="home">
    <header class="home__nav">
      <div class="home__brand">
        <span class="home__brand-mark"><IconSpark :size="16" /></span>
        财报智能体
      </div>
      <router-link class="home__enter" to="/workspace">进入工作台 →</router-link>
    </header>

    <main class="home__main">
      <h1 class="home__title">财报分析，像聊天一样自然</h1>
      <p class="home__subtitle">
        连接财报数据库与研报知识库：查指标、看趋势、比公司、问原因，一句话直接提问。
      </p>

      <div class="home__input">
        <ChatInput v-model="draft" placeholder="例如：药明康德 2024 年净利润是多少？" @send="startChat" />
      </div>

      <div class="home__examples">
        <button
          v-for="example in examplesStore.examples.slice(0, 6)"
          :key="example.id"
          class="home__example"
          type="button"
          @click="startChat(example.question)"
        >
          <span class="home__example-type">{{ example.type }}</span>
          {{ example.question }}
        </button>
      </div>

      <ul class="home__features">
        <li>SQL 直查财报数据库，数字可溯源</li>
        <li>研报 / 年报证据引用，支持归因分析</li>
        <li>自动绘制趋势与对比图表</li>
        <li>多轮对话追问，缺条件会先澄清</li>
      </ul>
    </main>
  </div>
</template>

<style scoped>
.home {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, var(--c-surface) 0%, var(--c-bg) 320px);
}

.home__nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-4) var(--sp-8);
}

.home__brand {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-weight: 600;
  font-size: var(--fs-lg);
}

.home__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
}

.home__enter {
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.home__enter:hover {
  color: var(--c-primary);
  text-decoration: none;
}

.home__main {
  flex: 1;
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
  padding: var(--sp-12) var(--sp-5);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.home__title {
  font-size: var(--fs-3xl);
  font-weight: 700;
  letter-spacing: -0.5px;
  line-height: 1.25;
}

.home__subtitle {
  margin-top: var(--sp-3);
  color: var(--c-text-secondary);
  font-size: var(--fs-lg);
  line-height: 1.7;
}

.home__input {
  width: 100%;
  margin-top: var(--sp-8);
  text-align: left;
}

.home__examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--sp-2);
  margin-top: var(--sp-5);
}

.home__example {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  border-radius: var(--r-full);
  padding: 6px var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: all var(--t-fast);
  max-width: 100%;
}

.home__example:hover {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

.home__example-type {
  flex-shrink: 0;
  font-size: var(--fs-xs);
  color: var(--c-primary);
  background: var(--c-primary-soft);
  border-radius: var(--r-full);
  padding: 1px 8px;
}

.home__features {
  margin: var(--sp-10) 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-3) var(--sp-6);
  font-size: var(--fs-sm);
  color: var(--c-text-tertiary);
  text-align: left;
}

.home__features li::before {
  content: "·";
  color: var(--c-primary);
  font-weight: 700;
  margin-right: 6px;
}

@media (max-width: 640px) {
  .home__features {
    grid-template-columns: 1fr;
  }

  .home__title {
    font-size: var(--fs-2xl);
  }
}
</style>
