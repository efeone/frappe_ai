/** Content block types — mirrors the block schema the AI agent emits. */

export interface TextBlock {
  type: "text";
  content: string;
}

export interface Dataset {
  name: string;
  values: (number | string)[];
}

export interface ChartData {
  labels: string[];
  datasets: Dataset[];
}

export interface ChartOptions {
  stacked?: boolean;
  currency?: string;
}

export type ChartType =
  | "bar"
  | "line"
  | "pie"
  | "funnel"
  | "heatmap"
  | "calendar";

export interface ChartBlock {
  type: "chart";
  chart_type: ChartType;
  data: ChartData;
  options?: ChartOptions;
  title?: string;
}

export interface Column {
  label: string;
  key: string;
  format?: string;
}

export interface DocRoute {
  doctype: string;
  name: string;
}

export interface TableRow {
  values: Record<string, unknown>;
  route?: DocRoute;
}

export interface TableBlock {
  type: "table";
  columns: Column[];
  rows: TableRow[];
  title?: string;
}

export type TrendDirection = "up" | "down" | "flat";

export interface KPIMetric {
  label: string;
  value: number | string;
  format?: string;
  trend?: TrendDirection;
  trend_value?: string;
}

export interface KPIBlock {
  type: "kpi";
  metrics: KPIMetric[];
}

export type StatusColor = "green" | "red" | "yellow" | "blue" | "gray";

export interface StatusItem {
  label: string;
  status: string;
  color: StatusColor;
  route?: DocRoute;
}

export interface StatusListBlock {
  type: "status_list";
  title?: string;
  items: StatusItem[];
}

/** A button under an answer that takes the reader into the Desk.
 *  `new_doc` opens a prefilled form, `list` a filtered list view,
 *  `open` a single document. */
export type ActionKind = "new_doc" | "list" | "open";

export interface ActionItem {
  label: string;
  kind: ActionKind;
  doctype: string;
  /** new_doc: field values to prefill. */
  values?: Record<string, unknown>;
  /** list: filters to apply to the list view. */
  filters?: Record<string, unknown>;
  /** open: the document name. */
  name?: string;
}

export interface ActionsBlock {
  type: "actions";
  items: ActionItem[];
}

export type ContentBlock =
  | TextBlock
  | ChartBlock
  | TableBlock
  | KPIBlock
  | StatusListBlock
  | ActionsBlock;

export type BlockType = ContentBlock["type"];
