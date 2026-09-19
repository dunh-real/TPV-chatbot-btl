import PptxGenJS from "pptxgenjs";
import { renderCover, renderImageText, renderText } from "./basicLayouts.js";
import { renderChart, renderTable } from "./dataLayouts.js";
import { renderFooter, renderHeader } from "./layoutUtils.js";
import { renderHubSpoke, renderPairedGrid } from "./overviewLayouts.js";
import { renderRelationship, renderThreeColumns, renderTimeline } from "./processLayouts.js";
import { DiagramData, RenderSlide } from "./types.js";

function renderDiagram(slide: PptxGenJS.Slide, diagram: DiagramData): void {
  const layouts = {
    paired_grid: renderPairedGrid,
    three_columns: renderThreeColumns,
    relationship: renderRelationship,
    hub_spoke: renderHubSpoke,
    timeline: renderTimeline,
  };
  layouts[diagram.diagram_type](slide, diagram);
}

export function renderSlide(
  pptx: PptxGenJS,
  data: RenderSlide,
  slideIndex: number,
  totalSlides: number,
): void {
  const slide = pptx.addSlide();
  if (data.speaker_notes) slide.addNotes(data.speaker_notes);
  if (data.kind === "cover") {
    renderCover(slide, data);
    return;
  }

  renderHeader(slide, data.title);
  if (data.kind === "diagram" && data.diagram) renderDiagram(slide, data.diagram);
  else if (data.kind === "chart") renderChart(pptx, slide, data);
  else if (data.kind === "table") renderTable(slide, data);
  else if (data.kind === "image_text") renderImageText(slide, data);
  else renderText(slide, data);
  renderFooter(slide, slideIndex, totalSlides);
}
