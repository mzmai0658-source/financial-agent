<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { ChartData } from "@/types/api";
import { assetUrl } from "@/services/api";

const props = defineProps<{
  chartData?: ChartData | null;
  imageUrl?: string;
  title?: string;
}>();

const chartEl = ref<HTMLDivElement | null>(null);
let chartInstance: { dispose: () => void; resize: () => void; setOption: (option: unknown) => void } | null = null;
let resizeObserver: ResizeObserver | null = null;

async function renderChart() {
  if (!props.chartData || !chartEl.value) return;
  const echarts = await import("echarts");
  if (!chartEl.value) return;
  chartInstance ??= echarts.init(chartEl.value);

  const { chart_type, title, x_data, y_data, y_label, series_name } = props.chartData;
  const baseColor = "#2456c4";

  const option: Record<string, unknown> = {
    title: { text: title, left: "center", textStyle: { fontSize: 13, fontWeight: 600 } },
    tooltip: { trigger: chart_type === "pie" ? "item" : "axis" },
    grid: { left: 56, right: 24, top: 48, bottom: 42 },
  };

  if (chart_type === "pie") {
    option.series = [
      {
        type: "pie",
        radius: ["38%", "66%"],
        data: x_data.map((name, idx) => ({ name, value: y_data[idx] })),
        label: { formatter: "{b}: {d}%" },
      },
    ];
  } else {
    option.xAxis = { type: "category", data: x_data, axisLabel: { rotate: x_data.length > 6 ? 30 : 0 } };
    option.yAxis = { type: "value", name: y_label ?? "" };
    option.series = [
      {
        type: chart_type === "line" ? "line" : "bar",
        name: series_name ?? title,
        data: y_data,
        itemStyle: { color: baseColor },
        ...(chart_type === "line"
          ? { smooth: true, symbolSize: 7, areaStyle: { opacity: 0.08 } }
          : { barMaxWidth: 36, itemStyle: { color: baseColor, borderRadius: [4, 4, 0, 0] } }),
      },
    ];
  }

  chartInstance.setOption(option);
}

onMounted(() => {
  renderChart();
  if (chartEl.value) {
    resizeObserver = new ResizeObserver(() => chartInstance?.resize());
    resizeObserver.observe(chartEl.value);
  }
});

watch(() => props.chartData, renderChart, { deep: true });

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  chartInstance?.dispose();
  chartInstance = null;
});
</script>

<template>
  <figure class="chart-card">
    <div v-if="chartData" ref="chartEl" class="chart-card__canvas" />
    <a v-else-if="imageUrl" :href="assetUrl(imageUrl)" target="_blank" rel="noopener">
      <img class="chart-card__image" :src="assetUrl(imageUrl)" :alt="title ?? '图表'" loading="lazy" />
    </a>
  </figure>
</template>

<style scoped>
.chart-card {
  margin: 0;
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  background: var(--c-surface);
  padding: var(--sp-3);
}

.chart-card__canvas {
  width: 100%;
  height: 300px;
}

.chart-card__image {
  display: block;
  width: 100%;
  border-radius: var(--r-sm);
}
</style>
