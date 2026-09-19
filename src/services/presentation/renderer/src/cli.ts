import fs from "fs";
import path from "path";
import { RenderPlan } from "./types.js";
import { renderDeck } from "./renderDeck.js";

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.error("Usage: node cli.js <render_plan_json_path> <output_pptx_path>");
    process.exit(1);
  }

  const planPath = path.resolve(args[0]);
  const outputPath = path.resolve(args[1]);

  if (!fs.existsSync(planPath)) {
    console.error(`Error: RenderPlan JSON file not found at '${planPath}'`);
    process.exit(1);
  }

  try {
    const rawContent = fs.readFileSync(planPath, "utf-8");
    const plan: RenderPlan = JSON.parse(rawContent);

    if (!plan.slides || !Array.isArray(plan.slides)) {
      throw new Error("Invalid RenderPlan format: 'slides' array is missing.");
    }

    console.log(`Rendering '${plan.deck_title}' (${plan.slides.length} slides) -> '${outputPath}'...`);
    const startTime = Date.now();
    await renderDeck(plan, outputPath);
    const elapsed = Date.now() - startTime;

    console.log(`Success: Generated PowerPoint presentation in ${elapsed}ms -> ${outputPath}`);
    process.exit(0);
  } catch (err: any) {
    console.error(`Error during PPTX rendering: ${err.message || err}`);
    if (err.stack) {
      console.error(err.stack);
    }
    process.exit(1);
  }
}

main();
