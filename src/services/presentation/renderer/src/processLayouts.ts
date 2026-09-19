import PptxGenJS from "pptxgenjs";
import { DiagramData } from "./types.js";
import { Theme } from "./theme.js";
import { addCardText, colorAt } from "./layoutUtils.js";

export function renderThreeColumns(slide: PptxGenJS.Slide, data: DiagramData): void {
  data.items.slice(0, 3).forEach((item, index) => {
    const x = 0.85 + index * 4.18;
    const color = colorAt(index);
    if (index < 2) {
      slide.addShape("chevron", {
        x: x + 3.65, y: 3.32, w: 0.52, h: 0.5,
        fill: { color: Theme.colors.textMuted }, line: { color: Theme.colors.textMuted },
      });
    }
    slide.addShape("roundRect", {
      x, y: 1.72, w: 3.62, h: 4.55,
      fill: { color }, line: { color: Theme.colors.primary, width: 1.2 },
    });
    slide.addText(item.group || `0${index + 1}`, {
      x: x + 0.25, y: 1.98, w: 0.8, h: 0.5,
      color: "FFFFFF", bold: true, fontSize: 17, margin: 0,
    });
    slide.addText(item.title.toUpperCase(), {
      x: x + 0.25, y: 2.52, w: 3.12, h: 1.0,
      color: "FFFFFF", bold: true, fontSize: 18,
      align: "center", valign: "middle", margin: 0.08, fit: "shrink",
    });
    slide.addText(item.description || "", {
      x: x + 0.3, y: 3.82, w: 3.02, h: 1.82,
      color: "FFFFFF", fontSize: 14, valign: "top", margin: 0.08, fit: "shrink",
    });
  });
}

export function renderTimeline(slide: PptxGenJS.Slide, data: DiagramData): void {
  const items = data.items.slice(0, 7);
  slide.addShape("ellipse", {
    x: 0.62, y: 2.23, w: 2.8, h: 2.8,
    fill: { color: Theme.colors.primary }, line: { color: Theme.colors.primary },
  });
  slide.addText(data.center_label || "Các giai đoạn", {
    x: 0.9, y: 2.58, w: 2.24, h: 2.0,
    color: "FFFFFF", bold: true, fontSize: 24,
    align: "center", valign: "middle", margin: 0.08, fit: "shrink",
  });
  slide.addShape("arc", {
    x: 2.62, y: 1.55, w: 1.7, h: 4.65,
    rotate: 270, fill: { color: "FFFFFF", transparency: 100 },
    line: { color: Theme.colors.primary, width: 1.7, dashType: "dash" },
  });
  const gap = 4.9 / Math.max(items.length, 1);
  items.forEach((item, index) => {
    const y = 1.42 + index * gap;
    const color = colorAt(index);
    slide.addShape("line", {
      x: 3.68, y: y + 0.35, w: 0.47, h: 0,
      line: { color, width: 1.4, dashType: "dash" },
    });
    slide.addShape("ellipse", {
      x: 3.38, y: y + 0.19, w: 0.32, h: 0.32,
      fill: { color: "FFFFFF" }, line: { color, width: 2.5 },
    });
    slide.addShape("roundRect", {
      x: 4.15, y, w: 8.05, h: Math.min(0.66, gap - 0.08),
      fill: { color }, line: { color },
    });
    slide.addText(`${String(index + 1).padStart(2, "0")}  ${item.title}`, {
      x: 4.38, y, w: 3.7, h: Math.min(0.66, gap - 0.08),
      color: "FFFFFF", bold: true, fontSize: 15, valign: "middle", margin: 0.04, fit: "shrink",
    });
    slide.addText(item.description || "", {
      x: 8.0, y, w: 3.88, h: Math.min(0.66, gap - 0.08),
      color: "FFFFFF", fontSize: 11.5, valign: "middle", margin: 0.04, fit: "shrink",
    });
  });
}

export function renderRelationship(slide: PptxGenJS.Slide, data: DiagramData): void {
  const items = data.items.slice(0, 3);
  const rowH = 4.9 / Math.max(items.length, 1);
  items.forEach((item, index) => {
    const y = 1.55 + index * rowH;
    const color = colorAt(index);
    slide.addShape("line", {
      x: 3.75, y: y + 0.62, w: 5.78, h: 0,
      line: { color: Theme.colors.textMuted, width: 1.4, dashType: "dash" },
    });
    slide.addShape("roundRect", {
      x: 0.82, y: y + 0.12, w: 3.15, h: 1.02,
      fill: { color: "FFFFFF" }, line: { color, width: 1.5 },
    });
    addCardText(slide, item.title, "", 1.0, y + 0.17, 2.8, 0.92, color);
    slide.addShape("ellipse", {
      x: 5.08, y, w: 2.05, h: 1.25,
      fill: { color }, line: { color: "FFFFFF", width: 1.5 },
    });
    slide.addText(item.group || data.center_label || "Liên kết", {
      x: 5.25, y: y + 0.15, w: 1.72, h: 0.95,
      color: "FFFFFF", bold: true, fontSize: 15,
      align: "center", valign: "middle", margin: 0.05, fit: "shrink",
    });
    slide.addShape("roundRect", {
      x: 8.28, y: y + 0.12, w: 4.2, h: 1.02,
      fill: { color: "FFFFFF" }, line: { color, width: 1.5 },
    });
    slide.addText(item.description || "", {
      x: 8.48, y: y + 0.2, w: 3.8, h: 0.86,
      color: Theme.colors.textDark, fontSize: 13.5,
      align: "center", valign: "middle", margin: 0.04, fit: "shrink",
    });
  });
}
