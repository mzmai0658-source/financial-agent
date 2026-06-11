<script setup lang="ts">
import { computed, ref } from "vue";

import { IconCopy, IconCheck, IconChevronDown, IconChevronRight, IconDatabase, IconExpand } from "@/components/ui/icons";

const props = defineProps<{
  sql: string;
  rowCount?: number | null;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
}>();

const emit = defineEmits<{ openDetail: [] }>();

const expanded = ref(false);
const copied = ref(false);

const displayColumns = computed(() => {
  if (props.columns?.length) return props.columns;
  if (props.rows?.length) return Object.keys(props.rows[0]);
  return [];
});

async function copySql() {
  try {
    await navigator.clipboard.writeText(props.sql);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* 剪贴板不可用时静默 */
  }
}
</script>

<template>
  <div class="sql-card">
    <div class="sql-card__head">
      <button class="sql-card__toggle" type="button" @click="expanded = !expanded">
        <component :is="expanded ? IconChevronDown : IconChevronRight" :size="14" />
        <IconDatabase :size="14" />
        <span>SQL 查询</span>
        <span v-if="rowCount !== undefined && rowCount !== null" class="sql-card__count">{{ rowCount }} 行</span>
      </button>
      <div class="sql-card__actions">
        <button class="sql-card__action" type="button" :title="copied ? '已复制' : '复制 SQL'" @click="copySql">
          <component :is="copied ? IconCheck : IconCopy" :size="14" />
        </button>
        <button class="sql-card__action" type="button" title="在证据栏查看" @click="emit('openDetail')">
          <IconExpand :size="14" />
        </button>
      </div>
    </div>

    <template v-if="expanded">
      <pre class="sql-card__code">{{ sql }}</pre>
      <div v-if="rows?.length" class="sql-card__table-wrap">
        <table class="sql-card__table">
          <thead>
            <tr>
              <th v-for="col in displayColumns" :key="col">{{ col }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in rows.slice(0, 10)" :key="idx">
              <td v-for="col in displayColumns" :key="col">{{ row[col] ?? "-" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sql-card {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  background: var(--c-surface);
  overflow: hidden;
}

.sql-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-2) var(--sp-3);
}

.sql-card__toggle {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  border: none;
  background: none;
  padding: 0;
  cursor: pointer;
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-text);
}

.sql-card__count {
  color: var(--c-text-tertiary);
  font-weight: 400;
  font-size: var(--fs-xs);
}

.sql-card__actions {
  display: flex;
  gap: var(--sp-1);
}

.sql-card__action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  background: none;
  border-radius: var(--r-sm);
  cursor: pointer;
  color: var(--c-text-tertiary);
  transition: all var(--t-fast);
}

.sql-card__action:hover {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.sql-card__code {
  margin: 0;
  padding: var(--sp-3);
  background: var(--c-code-bg);
  border-top: 1px solid var(--c-border);
  font-size: var(--fs-xs);
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--c-text);
}

.sql-card__table-wrap {
  overflow-x: auto;
  border-top: 1px solid var(--c-border);
}

.sql-card__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-xs);
}

.sql-card__table th,
.sql-card__table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
  font-family: var(--font-mono);
}

.sql-card__table th {
  background: var(--c-surface-muted);
  color: var(--c-text-secondary);
  font-weight: 500;
  position: sticky;
  top: 0;
}

.sql-card__table tr:last-child td {
  border-bottom: none;
}
</style>
