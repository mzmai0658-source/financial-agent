import { defineStore } from "pinia";

import { fetchExamples } from "@/services/api";
import type { ExampleItem } from "@/types/api";

const fallbackExamples: ExampleItem[] = [
  {
    id: "ex-1",
    type: "结构化查询",
    question: "药明康德 2024 年营业收入是多少？",
  },
  {
    id: "ex-2",
    type: "趋势分析",
    question: "凯莱英近三年营业收入和净利润的变化趋势是什么？请画图。",
  },
  {
    id: "ex-3",
    type: "排名查询",
    question: "2024 年净利润最高的前十家公司有哪些？",
  },
  {
    id: "ex-4",
    type: "现金流",
    question: "迪安诊断 2024 年净现金流怎么样？",
  },
];

export const useExamplesStore = defineStore("examples", {
  state: () => ({
    examples: [] as ExampleItem[],
    loaded: false,
  }),
  actions: {
    async ensureLoaded() {
      if (this.loaded) return;
      try {
        const data = await fetchExamples();
        this.examples = data.examples?.length ? data.examples : fallbackExamples;
      } catch {
        this.examples = fallbackExamples;
      }
      this.loaded = true;
    },
  },
});
