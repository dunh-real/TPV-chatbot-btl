import PptxGenJS from "pptxgenjs";
import { Theme } from "./theme.js";

export const palette = Theme.colors.chartPalette;

export function colorAt(index: number): string {
  return palette[index % palette.length];
}

export function addConnector(
  slide: PptxGenJS.Slide,
  start: [number, number],
  end: [number, number],
): void {
  const [x1, y1] = start;
  const [x2, y2] = end;
  const negativeSlope = (x2 - x1) * (y2 - y1) < 0;
  slide.addShape("line", {
    x: Math.min(x1, x2), y: Math.min(y1, y2),
    w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipV: negativeSlope,
    line: { color: Theme.colors.border, width: 2 },
  });
}

export function renderHeader(slide: PptxGenJS.Slide, title: string): void {
  slide.addText(title, {
    x: 0.8, y: 0.42, w: 11.73, h: 0.7,
    fontFace: Theme.fonts.title, fontSize: 28, bold: true,
    color: Theme.colors.primary, valign: "middle", margin: 0,
  });
  slide.addShape("line", {
    x: 0.8, y: 1.2, w: 11.73, h: 0,
    line: { color: Theme.colors.secondary, width: 2.5 },
  });
}

export function renderFooter(
  slide: PptxGenJS.Slide,
  slideIndex: number,
  totalSlides: number,
): void {
  slide.addText("TPV Presentation Engine", {
    x: 0.8, y: 6.92, w: 4, h: 0.25,
    fontFace: Theme.fonts.body, fontSize: 9, color: Theme.colors.textMuted,
    margin: 0,
  });
  slide.addText(`${slideIndex} / ${totalSlides}`, {
    x: 10.5, y: 6.92, w: 2, h: 0.25,
    fontFace: Theme.fonts.body, fontSize: 9, color: Theme.colors.textMuted,
    align: "right", margin: 0,
  });
}

export function addBodyText(
  slide: PptxGenJS.Slide,
  items: string[],
  x: number,
  y: number,
  w: number,
  h: number,
): void {
  if (!items.length) return;
  const runs = items.map((text) => ({
    text,
    options: {
      bullet: { type: "bullet" as const }, breakLine: true,
      fontFace: Theme.fonts.body, fontSize: 18,
      color: Theme.colors.textDark, paraSpaceAfterPt: 12,
    },
  }));
  slide.addText(runs, { x, y, w, h, valign: "top", breakLine: false, margin: 0.08 });
}

export function addCardText(
  slide: PptxGenJS.Slide,
  title: string,
  description: string,
  x: number,
  y: number,
  w: number,
  h: number,
  color: string,
): void {
  slide.addText([
    { text: title, options: { bold: true, breakLine: true, fontSize: 16, color } },
    { text: description, options: { fontSize: 12.5, color: Theme.colors.textDark } },
  ], { x, y, w, h, margin: 0.12, valign: "middle", fontFace: Theme.fonts.body, fit: "shrink" });
}
