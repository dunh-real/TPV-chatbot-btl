import PptxGenJS from "pptxgenjs";
import { RenderPlan } from "./types.js";
import { renderSlide } from "./renderSlide.js";

/**
 * Renders a full presentation deck from a RenderPlan and writes the resulting .pptx file.
 */
export async function renderDeck(plan: RenderPlan, outputPath: string): Promise<string> {
  const pptx = new PptxGenJS();

  // Set presentation properties
  pptx.title = plan.deck_title || "TPV Presentation";
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "TPV Presentation Generator";
  pptx.company = "TPV";

  const totalSlides = plan.slides.length;

  // Render each slide sequentially
  plan.slides.forEach((slideData, idx) => {
    renderSlide(pptx, slideData, idx + 1, totalSlides);
  });

  // Write presentation file
  await pptx.writeFile({ fileName: outputPath });
  return outputPath;
}
