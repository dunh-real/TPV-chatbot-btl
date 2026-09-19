import PptxGenJS from "pptxgenjs";
import { RenderSlide } from "./types.js";
import { Theme } from "./theme.js";
import { addBodyText } from "./layoutUtils.js";

export function renderCover(slide: PptxGenJS.Slide, data: RenderSlide): void {
  slide.background = { color: Theme.colors.bgLight };
  slide.addShape("rect", {
    x: 0, y: 0, w: 0.28, h: 7.5,
    fill: { color: Theme.colors.secondary }, line: { color: Theme.colors.secondary },
  });
  slide.addShape("rect", {
    x: 0.28, y: 0, w: 0.14, h: 7.5,
    fill: { color: Theme.colors.primary }, line: { color: Theme.colors.primary },
  });
  slide.addText(data.title, {
    x: 1.2, y: 2.05, w: 10.7, h: 1.8,
    fontFace: Theme.fonts.title, fontSize: 40, bold: true,
    color: Theme.colors.primary, valign: "bottom", margin: 0, fit: "shrink",
  });
  slide.addShape("line", {
    x: 1.2, y: 4.08, w: 4, h: 0,
    line: { color: Theme.colors.secondary, width: 3 },
  });
  const subtitle = data.subtitle || data.body_text?.[0] || "";
  if (subtitle) {
    slide.addText(subtitle, {
      x: 1.2, y: 4.3, w: 10.5, h: 1.1,
      fontFace: Theme.fonts.body, fontSize: 20,
      color: Theme.colors.textMuted, margin: 0, fit: "shrink",
    });
  }
}

export function renderText(slide: PptxGenJS.Slide, data: RenderSlide): void {
  addBodyText(slide, data.body_text || [], 1.05, 1.65, 11.15, 4.95);
}

export function renderImageText(slide: PptxGenJS.Slide, data: RenderSlide): void {
  addBodyText(slide, data.body_text || [], 0.95, 1.65, 6.4, 4.95);
  const image = { x: 7.75, y: 1.62, w: 4.65, h: 4.85 };
  if (data.image?.path) {
    slide.addImage({ path: data.image.path, ...image });
    return;
  }
  slide.addShape("roundRect", {
    ...image, rectRadius: 0.08,
    fill: { color: Theme.colors.bgLight },
    line: { color: Theme.colors.border, width: 1.2, dashType: "dash" },
  });
  slide.addText(data.image?.alt || "Minh họa ý tưởng", {
    ...image, fontFace: Theme.fonts.body, fontSize: 16,
    color: Theme.colors.textMuted, align: "center", valign: "middle",
  });
}
