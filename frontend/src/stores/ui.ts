import { defineStore } from "pinia";

import type { HealthResponse } from "@/types/api";

export type EvidenceTab = "sql" | "execution" | "chart" | "refs";

const RAIL_KEY = "financial-chat-agent-rail";

export const useUiStore = defineStore("ui", {
  state: () => ({
    error: "",
    health: null as HealthResponse | null,
    railExpanded: window.localStorage.getItem(RAIL_KEY) === "1",
    railTab: "sql" as EvidenceTab,
    /** 证据栏聚焦的 assistant 消息索引（messages 数组下标，-1 表示最新） */
    focusedMessageIndex: -1,
  }),
  actions: {
    setError(value: string) {
      this.error = value;
    },
    setHealth(value: HealthResponse | null) {
      this.health = value;
    },
    openRail(tab?: EvidenceTab, messageIndex?: number) {
      if (tab) this.railTab = tab;
      if (messageIndex !== undefined) this.focusedMessageIndex = messageIndex;
      this.railExpanded = true;
      window.localStorage.setItem(RAIL_KEY, "1");
    },
    collapseRail() {
      this.railExpanded = false;
      window.localStorage.setItem(RAIL_KEY, "0");
    },
    toggleRail(tab?: EvidenceTab) {
      if (this.railExpanded && (!tab || tab === this.railTab)) {
        this.collapseRail();
      } else {
        this.openRail(tab);
      }
    },
    focusMessage(index: number) {
      this.focusedMessageIndex = index;
    },
  },
});
