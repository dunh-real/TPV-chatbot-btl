import PptxGenJS from "pptxgenjs";
import { RenderSlide } from "./types.js";
import { Theme } from "./theme.js";

export function renderChart(
  pptx: PptxGenJS,
  slide: PptxGenJS.Slide,
  data: RenderSlide,
): void {
  if (!data.chart) return;
  let chartType = pptx.ChartType.bar;
  if (data.chart.chart_type === "line") chartType = pptx.ChartType.line;
  if (data.chart.chart_type === "pie") chartType = pptx.ChartType.pie;
  const series = data.chart.series.map((item) => ({
    name: item.name, labels: data.chart!.categories, values: item.values,
  }));
  slide.addChart(chartType, series, {
    x: 0.9, y: 1.55, w: 11.5, h: 5.1,
    showTitle: false, showLegend: true, legendPos: "b",
    chartColors: Theme.colors.chartPalette,
  });
}

export function renderTable(slide: PptxGenJS.Slide, data: RenderSlide): void {
  if (!data.table?.headers) return;
  const header: PptxGenJS.TableCell[] = data.table.headers.map((text) => ({
    text,
    options: {
      bold: true, color: Theme.colors.tableHeaderFg,
      fill: { color: Theme.colors.tableHeaderBg },
      fontFace: Theme.fonts.title, fontSize: 14, align: "center", margin: 0.08,
    },
  }));
  const rows: PptxGenJS.TableRow[] = data.table.rows.map((row, index) =>
    row.map((text) => ({
      text,
      options: {
        color: Theme.colors.textDark,
        fill: { color: index % 2 ? Theme.colors.tableZebra : Theme.colors.bgCard },
        fontFace: Theme.fonts.body, fontSize: 13, margin: 0.08,
      },
    })),
  );
  slide.addTable([header, ...rows], {
    x: 0.8, y: 1.55, w: 11.73, h: 4.95,
    autoPage: false, border: { pt: 1, color: Theme.colors.border },
  });
}
