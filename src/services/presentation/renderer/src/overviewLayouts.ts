import PptxGenJS from "pptxgenjs";
import { DiagramData } from "./types.js";
import { Theme } from "./theme.js";
import { addCardText, addConnector, colorAt } from "./layoutUtils.js";

export function renderPairedGrid(slide: PptxGenJS.Slide, data: DiagramData): void {
  const items = data.items.slice(0, 6);
  const rows = Math.ceil(items.length / 2);
  const rowH = Math.min(1.48, 4.75 / Math.max(rows, 1));
  items.forEach((item, index) => {
    const column = index % 2;
    const row = Math.floor(index / 2);
    const x = 0.95 + column * 6.05;
    const y = 1.55 + row * (rowH + 0.13);
    const color = colorAt(index);
    slide.addShape("ellipse", {
      x, y: y + 0.16, w: 0.72, h: 0.72,
      fill: { color }, line: { color }, shadow: { type: "outer", color: "B8C4D0", blur: 1, angle: 45, opacity: 0.2 },
    });
    slide.addText(String(index + 1).padStart(2, "0"), {
      x, y: y + 0.16, w: 0.72, h: 0.72,
      color: "FFFFFF", bold: true, fontSize: 14, align: "center", valign: "middle", margin: 0,
    });
    addCardText(slide, item.title, item.description || "", x + 0.86, y, 4.9, rowH, color);
  });
}

export function renderHubSpoke(slide: PptxGenJS.Slide, data: DiagramData): void {
  const items = data.items.slice(0, 6);
  const center = { x: 5.37, y: 3.0, w: 2.55, h: 1.35 };
  const allNodes = [
    { x: 0.95, y: 1.52 }, { x: 4.35, y: 1.45 }, { x: 8.85, y: 1.52 },
    { x: 0.95, y: 4.8 }, { x: 4.35, y: 5.0 }, { x: 8.85, y: 4.8 },
  ];
  const indexes = items.length === 4
    ? [0, 2, 3, 5]
    : items.length === 5 ? [0, 1, 2, 3, 5] : [0, 1, 2, 3, 4, 5];
  const nodes = indexes.map((index) => allNodes[index]);
  items.forEach((_, index) => {
    const node = nodes[index];
    addConnector(
      slide,
      [node.x + 1.7, node.y + 0.65],
      [center.x + center.w / 2, center.y + center.h / 2],
    );
  });
  slide.addShape("ellipse", {
    ...center, fill: { color: Theme.colors.primary },
    line: { color: Theme.colors.secondary, width: 3 },
  });
  slide.addText(data.center_label || "Trọng tâm", {
    ...center, color: "FFFFFF", bold: true, fontSize: 19,
    align: "center", valign: "middle", margin: 0.12, fit: "shrink",
  });
  items.forEach((item, index) => {
    const node = nodes[index];
    const color = colorAt(index);
    slide.addShape("roundRect", {
      x: node.x, y: node.y, w: 3.4, h: 1.3,
      fill: { color: "FFFFFF" }, line: { color, width: 2 },
      shadow: { type: "outer", color: "B8C4D0", blur: 1, angle: 45, opacity: 0.18 },
    });
    addCardText(slide, item.title, item.description || "", node.x + 0.08, node.y + 0.05, 3.24, 1.2, color);
  });
}
