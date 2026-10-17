import { useEffect, useMemo, useRef } from "react";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TooltipComponentOption,
} from "echarts/components";
import * as echarts from "echarts/core";
import type {
  BarSeriesOption,
  LineSeriesOption,
  PieSeriesOption,
} from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";

import type { ChartQueryResult, DashboardItemResult } from "../lib/api";
import { formatMetric } from "../lib/format";
import { useI18n, type InterfaceLocale } from "../lib/i18n";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

type ChartOption = echarts.ComposeOption<
  | BarSeriesOption
  | LineSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
>;

const palette = ["#4f67f6", "#18a999", "#8f66d8", "#e69f37", "#4b91c8"];

type RenderableChartResult = DashboardItemResult | ChartQueryResult;

export function ChartView({ result }: { result: RenderableChartResult }) {
  const { locale, t } = useI18n();
  const type = result.meta.chart_type;
  const measure = result.data.columns.find(
    (column) => column.role === "measure",
  );
  const firstRow = result.data.rows[0];

  if (type === "kpi") {
    return (
      <div className="kpi-view">
        <span>{measure?.label ?? t("Value")}</span>
        <strong>
          {formatMetric(measure && firstRow ? firstRow[measure.key] : null)}
        </strong>
        <small>
          {result.data.rows.length === 0
            ? t("No result for the saved filters")
            : t("Current snapshot result")}
        </small>
      </div>
    );
  }

  return (
    <>
      <EChart
        option={buildChartOption(result, locale)}
        label={t("{name} {type} chart", { name: result.meta.chart_name, type })}
      />
      <details className="chart-data">
        <summary>{t("View accessible data")}</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {result.data.columns.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.data.rows.map((row, index) => (
                <tr key={index}>
                  {result.data.columns.map((column) => (
                    <td key={column.key}>{formatMetric(row[column.key])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

function EChart({ option, label }: { option: ChartOption; label: string }) {
  const element = useRef<HTMLDivElement>(null);
  const stableOption = useMemo(() => option, [option]);

  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, undefined, {
      renderer: "canvas",
    });
    chart.setOption(stableOption);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [stableOption]);

  return (
    <div className="chart-canvas" ref={element} role="img" aria-label={label} />
  );
}

export function buildChartOption(
  result: RenderableChartResult,
  locale: InterfaceLocale = "en",
): ChartOption {
  const dimension = result.data.columns.find(
    (column) => column.role === "dimension",
  );
  const measures = result.data.columns.filter(
    (column) => column.role === "measure",
  );
  const labels = dimension
    ? result.data.rows.map((row) => formatDimension(row[dimension.key], locale))
    : [];
  const reduceMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const common: ChartOption = {
    animation: !reduceMotion,
    animationDuration: reduceMotion ? 0 : 420,
    color: palette,
    textStyle: {
      fontFamily: "Inter, ui-sans-serif, system-ui",
      color: "#4c5260",
    },
    tooltip: { trigger: "axis", confine: true },
  };

  if (result.meta.chart_type === "pie" || result.meta.chart_type === "donut") {
    const measure = measures[0];
    return {
      ...common,
      tooltip: { trigger: "item", confine: true },
      legend: { bottom: 0, type: "scroll", icon: "circle" },
      series: [
        {
          type: "pie",
          radius:
            result.meta.chart_type === "donut" ? ["48%", "72%"] : ["0%", "72%"],
          center: ["50%", "43%"],
          avoidLabelOverlap: true,
          itemStyle: {
            borderColor: "#ffffff",
            borderWidth: 2,
            borderRadius: 5,
          },
          label: { show: result.data.rows.length <= 8, formatter: "{b}" },
          data: result.data.rows.map((row, index) => ({
            name: labels[index] ?? `Row ${index + 1}`,
            value: numericValue(measure ? row[measure.key] : null),
          })),
        },
      ],
    };
  }

  const isLine = result.meta.chart_type === "line";
  return {
    ...common,
    grid: {
      left: 12,
      right: 14,
      top: 24,
      bottom: 20,
      outerBoundsMode: "same",
      outerBoundsContain: "axisLabel",
    },
    legend:
      measures.length > 1 ? { top: 0, right: 0, icon: "roundRect" } : undefined,
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: "#dfe2e8" } },
      axisTick: { show: false },
      axisLabel: { color: "#727887", hideOverlap: true },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#eceef2" } },
      axisLabel: { color: "#727887" },
    },
    series: measures.map((measure) => ({
      type: isLine ? "line" : "bar",
      name: measure.label,
      data: result.data.rows.map((row) => numericValue(row[measure.key])),
      ...(isLine
        ? {
            smooth: 0.25,
            symbolSize: 7,
            lineStyle: { width: 3 },
            areaStyle: { opacity: 0.06 },
          }
        : { barMaxWidth: 36, itemStyle: { borderRadius: [5, 5, 0, 0] } }),
    })) as BarSeriesOption[] | LineSeriesOption[],
  };
}

function numericValue(value: unknown): number {
  const converted = typeof value === "number" ? value : Number(value);
  return Number.isFinite(converted) ? converted : 0;
}

function formatDimension(value: unknown, locale: InterfaceLocale): string {
  if (typeof value !== "string") return formatMetric(value);
  const timestamp = Date.parse(value);
  if (!Number.isNaN(timestamp) && /^\d{4}-\d{2}-\d{2}/.test(value)) {
    return new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", { dateStyle: "medium" }).format(
      timestamp,
    );
  }
  return value;
}
