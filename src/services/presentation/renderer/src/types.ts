export interface ChartSeries {
  name: string;
  values: number[];
}

export interface ChartData {
  chart_type: 'bar' | 'line' | 'pie';
  categories: string[];
  series: ChartSeries[];
}

export interface TableData {
  headers: string[];
  rows: string[][];
}

export interface ImageData {
  url?: string;
  path?: string;
  alt?: string;
}

export type DiagramType =
  | 'paired_grid'
  | 'three_columns'
  | 'relationship'
  | 'hub_spoke'
  | 'timeline';

export interface DiagramItem {
  title: string;
  description?: string;
  group?: string;
}

export interface DiagramData {
  diagram_type: DiagramType;
  center_label?: string;
  items: DiagramItem[];
}

export interface RenderSlide {
  id: string;
  kind: 'cover' | 'text' | 'image_text' | 'chart' | 'table' | 'diagram';
  title: string;
  subtitle?: string | null;
  body_text?: string[];
  image?: ImageData | null;
  chart?: ChartData | null;
  table?: TableData | null;
  diagram?: DiagramData | null;
  speaker_notes?: string;
}

export interface RenderPlan {
  schema_version: string;
  deck_title: string;
  language?: string;
  total_slides?: number;
  slides: RenderSlide[];
}
