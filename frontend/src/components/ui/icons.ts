import { h, type FunctionalComponent } from "vue";

interface IconProps {
  size?: number | string;
}

function makeIcon(paths: string[], viewBox = "0 0 24 24"): FunctionalComponent<IconProps> {
  const icon: FunctionalComponent<IconProps> = (props) =>
    h(
      "svg",
      {
        width: props.size ?? 16,
        height: props.size ?? 16,
        viewBox,
        fill: "none",
        stroke: "currentColor",
        "stroke-width": 1.8,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        "aria-hidden": "true",
      },
      paths.map((d) => h("path", { d })),
    );
  icon.props = { size: { type: [Number, String], required: false } };
  return icon;
}

export const IconSend = makeIcon(["M22 2 11 13", "M22 2 15 22l-4-9-9-4 20-7z"]);
export const IconStop = makeIcon(["M7 7h10v10H7z"]);
export const IconPlus = makeIcon(["M12 5v14", "M5 12h14"]);
export const IconCopy = makeIcon(["M9 9h11v11H9z", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"]);
export const IconCheck = makeIcon(["M20 6 9 17l-5-5"]);
export const IconClose = makeIcon(["M18 6 6 18", "M6 6l12 12"]);
export const IconChevronDown = makeIcon(["m6 9 6 6 6-6"]);
export const IconChevronRight = makeIcon(["m9 18 6-6-6-6"]);
export const IconDatabase = makeIcon([
  "M12 8c4.97 0 9-1.34 9-3s-4.03-3-9-3-9 1.34-9 3 4.03 3 9 3z",
  "M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5",
  "M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3",
]);
export const IconDocument = makeIcon([
  "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z",
  "M14 2v6h6",
  "M9 13h6",
  "M9 17h6",
]);
export const IconChart = makeIcon(["M3 3v18h18", "M7 14v4", "M12 9v9", "M17 5v13"]);
export const IconList = makeIcon(["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"]);
export const IconSpark = makeIcon([
  "M12 2 13.8 8.2 20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z",
  "M19 15l.9 3.1L23 19l-3.1.9L19 23l-.9-3.1L15 19l3.1-.9z",
]);
export const IconExpand = makeIcon(["M15 3h6v6", "M9 21H3v-6", "M21 3l-7 7", "M3 21l7-7"]);
export const IconAlert = makeIcon([
  "M12 2 1 21h22z",
  "M12 9v5",
  "M12 18h.01",
]);
export const IconRefresh = makeIcon([
  "M3 12a9 9 0 0 1 15.5-6.2L21 8",
  "M21 3v5h-5",
  "M21 12a9 9 0 0 1-15.5 6.2L3 16",
  "M3 21v-5h5",
]);
export const IconArrowUp = makeIcon(["M12 19V5", "m5 12 7-7 7 7"]);
export const IconPanelRight = makeIcon([
  "M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  "M15 3v18",
]);
