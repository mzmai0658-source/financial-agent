<script setup lang="ts">
import { computed } from "vue";

import type { MessageView } from "@/components/chat/messageView";
import ThinkingTrail from "@/components/chat/ThinkingTrail.vue";
import ChartCard from "@/components/chat/ChartCard.vue";
import ReferenceCards from "@/components/chat/ReferenceCards.vue";
import BaseEmpty from "@/components/ui/BaseEmpty.vue";
import { IconChart, IconClose, IconCopy, IconDatabase, IconDocument, IconList } from "@/components/ui/icons";
import { useUiStore, type EvidenceTab } from "@/stores/ui";

const props = defineProps<{
  view: MessageView | null;
}>();

const ui = useUiStore();

const tabs = computed(() => [
  { key: "sql" as EvidenceTab, label: "SQL", icon: IconDatabase, count: props.view?.sql ? 1 : 0 },
  { key: "execution" as EvidenceTab, label: "执行", icon: IconList, count: props.view?.steps.length ?? 0 },
  { key: "chart" as EvidenceTab, label: "图表", icon: IconChart, count: props.view?.charts.length ?? 0 },
  { key: "refs" as EvidenceTab, label: "引用", icon: IconDocument, count: props.view?.references.length ?? 0 },
]);

const sqlStatements = computed(() =>
  (props.view?.sql ?? "")
    .split(";\n")
    .map((statement) => statement.trim())
    .filter(Boolean),
);

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    /* 剪贴板不可用时静默 */
  }
}
</script>

<template>
  <aside class="rail" :class="{ 'rail--expanded': ui.railExpanded }">
    <!-- 收起态：图标条 -->
    <div v-if="!ui.railExpanded" class="rail__strip">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="rail__strip-btn"
        type="button"
        :title="tab.label"
        @click="ui.openRail(tab.key)"
      >
        <component :is="tab.icon" :size="17" />
        <span v-if="tab.count" class="rail__badge">{{ tab.count }}</span>
      </button>
    </div>

    <!-- 展开态：证据面板 -->
    <div v-else class="rail__panel">
      <header class="rail__head">
        <nav class="rail__tabs">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            class="rail__tab"
            :class="{ 'rail__tab--active': ui.railTab === tab.key }"
            type="button"
            @click="ui.railTab = tab.key"
          >
            <component :is="tab.icon" :size="14" />
            {{ tab.label }}
            <span v-if="tab.count" class="rail__tab-count">{{ tab.count }}</span>
          </button>
        </nav>
        <button class="rail__close" type="button" title="收起" @click="ui.collapseRail()">
          <IconClose :size="15" />
        </button>
      </header>

      <div class="rail__content">
        <template v-if="!view">
          <BaseEmpty text="发起提问后，这里会展示证据详情" />
        </template>

        <!-- SQL -->
        <template v-else-if="ui.railTab === 'sql'">
          <BaseEmpty v-if="!sqlStatements.length" text="本轮没有执行 SQL" />
          <div v-for="(statement, idx) in sqlStatements" :key="idx" class="rail__sql">
            <div class="rail__sql-head">
              <span>语句 {{ idx + 1 }}</span>
              <button class="rail__icon-btn" type="button" title="复制" @click="copyText(statement)">
                <IconCopy :size="13" />
              </button>
            </div>
            <pre class="rail__code">{{ statement }}</pre>
          </div>
          <div v-if="view.rows?.length" class="rail__table-wrap">
            <table class="rail__table">
              <thead>
                <tr>
                  <th v-for="col in view.columns?.length ? view.columns : Object.keys(view.rows[0])" :key="col">
                    {{ col }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in view.rows" :key="idx">
                  <td v-for="col in view.columns?.length ? view.columns : Object.keys(view.rows[0])" :key="col">
                    {{ row[col] ?? "-" }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <!-- 执行过程 -->
        <template v-else-if="ui.railTab === 'execution'">
          <BaseEmpty v-if="!view.steps.length" text="暂无执行记录" />
          <ThinkingTrail v-else :steps="view.steps" :live="view.live" />
        </template>

        <!-- 图表 -->
        <template v-else-if="ui.railTab === 'chart'">
          <BaseEmpty v-if="!view.charts.length" text="本轮没有生成图表" />
          <ChartCard
            v-for="(chart, idx) in view.charts"
            :key="idx"
            :chart-data="chart.chartData"
            :image-url="chart.url"
            :title="chart.title"
          />
        </template>

        <!-- 引用 -->
        <template v-else>
          <BaseEmpty v-if="!view.references.length" text="本轮没有引用研报/年报" />
          <ReferenceCards v-else :references="view.references" />
        </template>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.rail {
  flex-shrink: 0;
  width: var(--rail-right-collapsed);
  border-left: 1px solid var(--c-border);
  background: var(--c-surface);
  transition: width var(--t-base);
  height: 100%;
  overflow: hidden;
}

.rail--expanded {
  width: var(--rail-right-expanded);
}

.rail__strip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-2);
  padding-top: var(--sp-4);
}

.rail__strip-btn {
  position: relative;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--r-md);
  background: none;
  color: var(--c-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all var(--t-fast);
}

.rail__strip-btn:hover {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.rail__badge {
  position: absolute;
  top: 1px;
  right: 1px;
  min-width: 14px;
  height: 14px;
  padding: 0 3px;
  border-radius: var(--r-full);
  background: var(--c-primary);
  color: #fff;
  font-size: 10px;
  line-height: 14px;
  text-align: center;
}

.rail__panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: var(--rail-right-expanded);
}

.rail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}

.rail__tabs {
  display: flex;
  gap: var(--sp-1);
}

.rail__tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 var(--sp-2);
  border: none;
  border-radius: var(--r-sm);
  background: none;
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: all var(--t-fast);
}

.rail__tab:hover {
  color: var(--c-primary);
}

.rail__tab--active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 500;
}

.rail__tab-count {
  font-size: 10px;
  background: var(--c-border);
  border-radius: var(--r-full);
  padding: 0 5px;
  line-height: 14px;
  color: var(--c-text-secondary);
}

.rail__tab--active .rail__tab-count {
  background: var(--c-primary);
  color: #fff;
}

.rail__close {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--r-sm);
  background: none;
  color: var(--c-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.rail__close:hover {
  background: var(--c-bg);
  color: var(--c-text);
}

.rail__content {
  flex: 1;
  overflow-y: auto;
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.rail__sql {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  overflow: hidden;
}

.rail__sql-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-1) var(--sp-2) var(--sp-1) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
  background: var(--c-surface-muted);
  border-bottom: 1px solid var(--c-border);
}

.rail__icon-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  border-radius: var(--r-sm);
  color: var(--c-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.rail__icon-btn:hover {
  color: var(--c-primary);
  background: var(--c-primary-soft);
}

.rail__code {
  margin: 0;
  padding: var(--sp-3);
  font-size: var(--fs-xs);
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--c-code-bg);
}

.rail__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
}

.rail__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-xs);
  font-family: var(--font-mono);
}

.rail__table th,
.rail__table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}

.rail__table th {
  background: var(--c-surface-muted);
  color: var(--c-text-secondary);
  font-weight: 500;
}

.rail__table tr:last-child td {
  border-bottom: none;
}
</style>
