import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Braces,
  Check,
  Donut,
  Gauge,
  LineChart,
  LoaderCircle,
  PieChart,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useOutletContext, useParams, useSearchParams } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  createChart,
  getChart,
  getDatasetExploration,
  listDatasets,
  previewChart,
  problemMessage,
  replaceChart,
  type ChartDefinition,
  type ChartQueryResult,
  type CurrentUser,
  type DatasetColumn,
  type DatasetExploration,
} from "../lib/api";
import {
  aggregationLabels,
  aggregationsFor,
  changeChartType,
  chartDefinitionIsReady,
  createChartDefinition,
  definitionWithFilters,
  filterDraftsFromDefinition,
  isDimensionColumn,
  isRowCount,
  isTemporalColumn,
  isTimestampColumn,
  newMeasure,
  rowCountColumn,
  withNaturalOrder,
  type ChartAggregation,
  type ChartTimeBucket,
  type ChartType,
} from "../lib/chartDefinition";
import {
  inputType,
  newFilterDraft,
  operatorLabels,
  valuePlaceholder,
  type FilterDraft,
} from "../lib/datasetQuery";
import { formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { queryKeys } from "../lib/query";
import "./ChartStudioPage.css";

const ChartView = lazy(() =>
  import("../components/ChartView").then((module) => ({ default: module.ChartView })),
);

type Translate = ReturnType<typeof useI18n>["t"];
type Measure = ChartDefinition["measures"][number];

const CHART_TYPES: { value: ChartType; label: string; icon: typeof BarChart3 }[] = [
  { value: "bar", label: "Bars", icon: BarChart3 },
  { value: "line", label: "Line", icon: LineChart },
  { value: "pie", label: "Pie", icon: PieChart },
  { value: "donut", label: "Ring", icon: Donut },
  { value: "kpi", label: "One number", icon: Gauge },
];
const BUCKETS: { value: "" | ChartTimeBucket; label: string }[] = [
  { value: "", label: "Each value" },
  { value: "hour", label: "By hour" },
  { value: "day", label: "By day" },
  { value: "week", label: "By week" },
  { value: "month", label: "By month" },
  { value: "quarter", label: "By quarter" },
  { value: "year", label: "By year" },
];
const ROW_COUNT = "rows";

export function ChartStudioPage() {
  const user = useOutletContext<CurrentUser>();
  const { chartId } = useParams();
  if (user.role === "viewer") return <Navigate to="/overview" replace />;
  return chartId ? <ExistingChartStudio chartId={chartId} /> : <NewChartStudio />;
}

function NewChartStudio() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState(() => searchParams.get("dataset") ?? "");
  const [definition, setDefinition] = useState<ChartDefinition | null>(null);
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [setupError, setSetupError] = useState("");
  const datasets = useQuery({ queryKey: queryKeys.chartDatasets, queryFn: () => listDatasets() });
  const exploration = useQuery({
    queryKey: queryKeys.datasetExploration(datasetId || "none"),
    queryFn: () => getDatasetExploration(datasetId),
    enabled: Boolean(datasetId),
  });

  useEffect(() => {
    if (!exploration.data) return;
    try {
      const created = createChartDefinition(exploration.data.columns);
      const columns = exploration.data.columns;
      setDefinition({
        ...created,
        measures: created.measures.map((measure) => ({ ...measure, label: measureLabel(measure, columns, t) })),
      });
      setFilters([]);
      setSetupError("");
    } catch (error) {
      setDefinition(null);
      setSetupError(error instanceof Error ? t(error.message) : t("This dataset has no chartable fields."));
    }
  }, [exploration.data, t]);

  const prepared = usePreparedDefinition(definition, filters, exploration.data);
  const create = useMutation({
    mutationFn: () => {
      if (!prepared.definition) throw new Error(prepared.error || t("Finish the chart definition."));
      return createChart(name.trim(), datasetId, prepared.definition);
    },
    onSuccess: async ({ chart, etag }) => {
      queryClient.setQueryData(queryKeys.chart(chart.id), { chart, etag });
      await queryClient.invalidateQueries({ queryKey: queryKeys.charts });
      await navigate(`/charts/${chart.id}`, { replace: true });
    },
  });
  const loaded = datasets.data?.data.filter((dataset) => dataset.active_snapshot_id !== null) ?? [];
  const ready = Boolean(name.trim() && prepared.definition && chartDefinitionIsReady(prepared.definition));

  return (
    <ChartWorkspace
      name={name}
      onNameChange={setName}
      source={
        <label className="chart-source">
          <span>{t("Data")}</span>
          <select
            value={datasetId}
            onChange={(event) => {
              setDatasetId(event.target.value);
              setDefinition(null);
              setSetupError("");
            }}
          >
            <option value="">{t("Choose a dataset")}</option>
            {loaded.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>{dataset.name}</option>
            ))}
          </select>
        </label>
      }
      status={
        !datasetId
          ? t("Choose the data to chart")
          : !name.trim()
            ? t("Give the chart a name to save it")
            : t("Not saved yet")
      }
      action={
        <button className="button primary" type="button" disabled={!ready || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? <LoaderCircle className="spin" /> : <Save />} {t("Save chart")}
        </button>
      }
      error={setupError || prepared.error || (create.isError ? problemMessage(create.error) : "")}
      empty={
        datasets.isError ? (
          <InlineError message={problemMessage(datasets.error)} />
        ) : !datasets.isPending && loaded.length === 0 ? (
          <div className="chart-empty">
            <strong>{t("There is no loaded data yet")}</strong>
            <p>{t("Create a dataset and load its data first.")}</p>
            <Link className="button secondary" to="/datasets/new">{t("New dataset")}</Link>
          </div>
        ) : exploration.isError ? (
          <InlineError message={problemMessage(exploration.error)} />
        ) : !datasetId ? (
          <div className="chart-empty">
            <strong>{t("Choose the data to chart")}</strong>
            <p>{t("The chart appears here as soon as you pick a dataset.")}</p>
          </div>
        ) : (
          <div className="chart-empty skeleton" />
        )
      }
      datasetId={datasetId}
      definition={definition}
      onDefinitionChange={setDefinition}
      filters={filters}
      onFiltersChange={setFilters}
      exploration={exploration.data}
      previewDefinition={prepared.definition}
    />
  );
}

function ExistingChartStudio({ chartId }: { chartId: string }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<ChartDefinition | null>(null);
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [etag, setEtag] = useState("");
  const initialized = useRef(false);
  const chart = useQuery({ queryKey: queryKeys.chart(chartId), queryFn: () => getChart(chartId) });
  const datasetId = chart.data?.chart.dataset_id ?? "";
  const exploration = useQuery({
    queryKey: queryKeys.datasetExploration(datasetId || "none"),
    queryFn: () => getDatasetExploration(datasetId),
    enabled: Boolean(datasetId),
  });

  useEffect(() => {
    if (!initialized.current && chart.data) {
      initialized.current = true;
      setName(chart.data.chart.name);
      setDefinition(chart.data.chart.definition);
      setFilters(filterDraftsFromDefinition(chart.data.chart.definition));
      setEtag(chart.data.etag);
    }
  }, [chart.data]);

  const prepared = usePreparedDefinition(definition, filters, exploration.data);
  const saved = chart.data?.chart;
  const savedDefinition =
    saved && exploration.data
      ? safeDefinition(saved.definition, filterDraftsFromDefinition(saved.definition), exploration.data.columns)
      : null;
  const dirty = Boolean(
    saved &&
      prepared.definition &&
      (name !== saved.name || JSON.stringify(prepared.definition) !== JSON.stringify(savedDefinition)),
  );
  const save = useMutation({
    mutationFn: () => {
      if (!prepared.definition) throw new Error(prepared.error || t("Finish the chart definition."));
      return replaceChart(chartId, name.trim(), prepared.definition, etag);
    },
    onSuccess: async (value) => {
      queryClient.setQueryData(queryKeys.chart(chartId), value);
      setName(value.chart.name);
      setDefinition(value.chart.definition);
      setFilters(filterDraftsFromDefinition(value.chart.definition));
      setEtag(value.etag);
      await queryClient.invalidateQueries({ queryKey: queryKeys.charts });
    },
  });

  if (chart.isError || exploration.isError)
    return (
      <InlineError
        message={problemMessage(chart.error ?? exploration.error)}
        onRetry={() => {
          void chart.refetch();
          void exploration.refetch();
        }}
      />
    );
  if (!saved || !definition || !exploration.data) return <div className="dataset-table-skeleton skeleton" />;
  const ready = Boolean(name.trim() && prepared.definition && chartDefinitionIsReady(prepared.definition));

  return (
    <ChartWorkspace
      name={name}
      onNameChange={setName}
      source={
        <div className="chart-source">
          <span>{t("Data")}</span>
          <Link to={`/datasets/${datasetId}/table`}>{exploration.data.dataset_name}</Link>
        </div>
      }
      status={t(dirty ? "Unsaved changes" : "Saved")}
      action={
        <button className="button primary" type="button" disabled={!dirty || !ready || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <LoaderCircle className="spin" /> : <Save />} {t("Save changes")}
        </button>
      }
      error={prepared.error || (save.isError ? problemMessage(save.error) : "")}
      empty={null}
      datasetId={datasetId}
      definition={definition}
      onDefinitionChange={setDefinition}
      filters={filters}
      onFiltersChange={setFilters}
      exploration={exploration.data}
      previewDefinition={prepared.definition}
    />
  );
}

function ChartWorkspace({
  name,
  onNameChange,
  source,
  status,
  action,
  error,
  empty,
  datasetId,
  definition,
  onDefinitionChange,
  filters,
  onFiltersChange,
  exploration,
  previewDefinition,
}: {
  name: string;
  onNameChange: (value: string) => void;
  source: React.ReactNode;
  status: string;
  action: React.ReactNode;
  error: string;
  empty: React.ReactNode;
  datasetId: string;
  definition: ChartDefinition | null;
  onDefinitionChange: (value: ChartDefinition) => void;
  filters: FilterDraft[];
  onFiltersChange: (value: FilterDraft[]) => void;
  exploration?: DatasetExploration;
  previewDefinition: ChartDefinition | null;
}) {
  const { t } = useI18n();
  return (
    <div className="chart-studio page-stack">
      <header className="chart-studio-bar">
        <Link className="back-link" to="/charts">
          <ArrowLeft /> {t("Charts")}
        </Link>
        <input
          className="chart-name"
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
          placeholder={t("Chart name")}
          aria-label={t("Chart name")}
          maxLength={100}
        />
        {source}
        <span className="chart-status">{status}</span>
        {action}
      </header>
      {error && <InlineError message={error} />}
      {definition && exploration ? (
        <div className="chart-studio-layout">
          <ChartSettings
            definition={definition}
            onChange={onDefinitionChange}
            filters={filters}
            onFiltersChange={onFiltersChange}
            exploration={exploration}
          />
          <LivePreview datasetId={datasetId} name={name} definition={previewDefinition} timezone={exploration.installation_timezone} />
        </div>
      ) : (
        empty
      )}
    </div>
  );
}

function ChartSettings({
  definition,
  onChange,
  filters,
  onFiltersChange,
  exploration,
}: {
  definition: ChartDefinition;
  onChange: (value: ChartDefinition) => void;
  filters: FilterDraft[];
  onFiltersChange: (value: FilterDraft[]) => void;
  exploration: DatasetExploration;
}) {
  const { t } = useI18n();
  const columns = exploration.columns;
  const dimension = definition.dimensions?.[0];
  const dimensionColumn = columns.find((column) => column.field_id === dimension?.field_id);
  const several = ["bar", "line"].includes(definition.chart_type);
  const setMeasure = (id: string, next: Measure) =>
    onChange({
      ...definition,
      measures: definition.measures.map((item) => (item.id === id ? next : item)),
    });

  return (
    <aside className="chart-settings" aria-label={t("Chart settings")}>
      <section>
        <h2>{t("How to show it")}</h2>
        <div className="chart-type-picker" role="radiogroup" aria-label={t("Chart type")}>
          {CHART_TYPES.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={definition.chart_type === value}
              className={definition.chart_type === value ? "selected" : ""}
              onClick={() => onChange(changeChartType(definition, value, columns))}
            >
              <Icon />
              <span>{t(label)}</span>
            </button>
          ))}
        </div>
      </section>

      {definition.chart_type !== "kpi" && (
        <section>
          <h2>{t("Group by")}</h2>
          <select
            aria-label={t("Group by")}
            value={dimension?.field_id ?? ""}
            onChange={(event) => {
              const next = columns.find((column) => column.field_id === event.target.value)!;
              onChange(
                withNaturalOrder(
                  { ...definition, dimensions: [{ field_id: next.field_id, time_bucket: isTemporalColumn(next) ? "month" : null }] },
                  columns,
                ),
              );
            }}
          >
            {columns.filter(isDimensionColumn).map((column) => (
              <option key={column.field_id} value={column.field_id}>{column.label}</option>
            ))}
          </select>
          {dimensionColumn && isTemporalColumn(dimensionColumn) && (
            <select
              aria-label={t("Time step")}
              value={dimension?.time_bucket ?? ""}
              onChange={(event) =>
                onChange({
                  ...definition,
                  dimensions: [{ field_id: dimension!.field_id, time_bucket: (event.target.value as ChartTimeBucket) || null }],
                })
              }
            >
              {BUCKETS.filter((bucket) => bucket.value !== "hour" || isTimestampColumn(dimensionColumn)).map((bucket) => (
                <option key={bucket.value} value={bucket.value}>{t(bucket.label)}</option>
              ))}
            </select>
          )}
        </section>
      )}

      <section>
        <h2>{definition.chart_type === "kpi" ? t("Which number") : t("What to count")}</h2>
        {definition.measures.map((measure) => (
          <MeasureEditor
            key={measure.id}
            measure={measure}
            columns={columns}
            onChange={(next) => setMeasure(measure.id, next)}
            onRemove={
              several && definition.measures.length > 1
                ? () =>
                    onChange({
                      ...definition,
                      measures: definition.measures.filter((item) => item.id !== measure.id),
                      sorts: (definition.sorts ?? []).filter((sort) => sort.target_id !== measure.id),
                    })
                : undefined
            }
          />
        ))}
        {several && definition.measures.length < 5 && (
          <button
            className="button ghost small"
            type="button"
            onClick={() => {
              const column = rowCountColumn(columns)!;
              const added = newMeasure(column, "count");
              onChange({ ...definition, measures: [...definition.measures, { ...added, label: measureLabel(added, columns, t) }] });
            }}
          >
            <Plus /> {t("Add another value")}
          </button>
        )}
      </section>

      {definition.chart_type !== "kpi" && <OrderSettings definition={definition} onChange={onChange} />}

      <details className="chart-more">
        <summary>{t("Only part of the data")}{filters.length ? ` · ${filters.length}` : ""}</summary>
        <FilterList columns={columns} filters={filters} onChange={onFiltersChange} />
      </details>

      <details className="chart-more">
        <summary><Braces /> {t("For specialists: chart definition (JSON)")}</summary>
        <pre>{JSON.stringify(safeDefinition(definition, filters, columns), null, 2)}</pre>
      </details>
    </aside>
  );
}

function MeasureEditor({
  measure,
  columns,
  onChange,
  onRemove,
}: {
  measure: Measure;
  columns: DatasetColumn[];
  onChange: (value: Measure) => void;
  onRemove?: () => void;
}) {
  const { t } = useI18n();
  const rowCount = isRowCount(measure, columns);
  const how = rowCount ? ROW_COUNT : measure.aggregation;
  const fieldColumns = columns.filter((column) =>
    aggregationsFor(column).includes((how === ROW_COUNT ? "count" : how) as ChartAggregation),
  );
  const update = (aggregation: ChartAggregation, fieldId: string) => {
    const next = { ...measure, aggregation, field_id: fieldId };
    const autoLabel = measure.label === measureLabel(measure, columns, t);
    onChange({ ...next, label: autoLabel ? measureLabel(next, columns, t) : measure.label });
  };
  return (
    <div className="measure-editor">
      <select
        aria-label={t("How to count")}
        value={how}
        onChange={(event) => {
          const value = event.target.value;
          if (value === ROW_COUNT) return update("count", rowCountColumn(columns)!.field_id);
          const aggregation = value as ChartAggregation;
          const current = columns.find((column) => column.field_id === measure.field_id);
          const field =
            current && aggregationsFor(current).includes(aggregation) && !rowCount
              ? current
              : columns.find((column) => aggregationsFor(column).includes(aggregation))!;
          update(aggregation, field.field_id);
        }}
      >
        <option value={ROW_COUNT}>{t("Number of rows")}</option>
        {(["count_distinct", "sum", "avg", "min", "max"] as ChartAggregation[])
          .filter((aggregation) => columns.some((column) => aggregationsFor(column).includes(aggregation)))
          .map((aggregation) => (
            <option key={aggregation} value={aggregation}>{t(AGGREGATION_NAMES[aggregation])}</option>
          ))}
      </select>
      {!rowCount && (
        <select
          aria-label={t("Of which column")}
          value={measure.field_id}
          onChange={(event) => update(measure.aggregation, event.target.value)}
        >
          {fieldColumns.map((column) => (
            <option key={column.field_id} value={column.field_id}>{column.label}</option>
          ))}
        </select>
      )}
      <input
        aria-label={t("Label")}
        value={measure.label}
        maxLength={100}
        onChange={(event) => onChange({ ...measure, label: event.target.value })}
      />
      {onRemove && (
        <button className="icon-button" type="button" aria-label={t("Remove {name}", { name: measure.label })} onClick={onRemove}>
          <Trash2 />
        </button>
      )}
    </div>
  );
}

const AGGREGATION_NAMES: Record<ChartAggregation, string> = {
  count: "Number of values",
  count_distinct: "Number of different values",
  sum: "Sum",
  avg: "Average",
  min: "Smallest",
  max: "Largest",
};

function measureLabel(measure: Measure, columns: DatasetColumn[], t: Translate): string {
  if (isRowCount(measure, columns)) return t("Number of rows");
  const column = columns.find((item) => item.field_id === measure.field_id);
  return `${t(AGGREGATION_NAMES[measure.aggregation] ?? aggregationLabels[measure.aggregation])}: ${column?.label ?? ""}`.slice(0, 100);
}

function OrderSettings({ definition, onChange }: { definition: ChartDefinition; onChange: (value: ChartDefinition) => void }) {
  const { t } = useI18n();
  const dimension = definition.dimensions?.[0];
  const measure = definition.measures[0];
  const sort = definition.sorts?.[0];
  const value = !sort ? "" : `${sort.target_type}:${sort.direction}`;
  return (
    <section>
      <h2>{t("Order")}</h2>
      <select
        aria-label={t("Order")}
        value={value}
        onChange={(event) => {
          if (!event.target.value) return onChange({ ...definition, sorts: [] });
          const [targetType, direction] = event.target.value.split(":") as ["dimension" | "measure", "asc" | "desc"];
          onChange({
            ...definition,
            sorts: [{ target_type: targetType, target_id: targetType === "measure" ? measure.id : dimension!.field_id, direction }],
          });
        }}
      >
        <option value="measure:desc">{t("Biggest first")}</option>
        <option value="measure:asc">{t("Smallest first")}</option>
        <option value="dimension:asc">{t("By group, A to Z or oldest first")}</option>
        <option value="dimension:desc">{t("By group, Z to A or newest first")}</option>
        <option value="">{t("As stored")}</option>
      </select>
      <label className="chart-limit">
        <span>{t("Show at most")}</span>
        <input
          type="number"
          min={1}
          max={1000}
          value={definition.limit}
          onChange={(event) => onChange({ ...definition, limit: Number(event.target.value) })}
        />
        <span>{t("groups")}</span>
      </label>
    </section>
  );
}

function FilterList({ columns, filters, onChange }: { columns: DatasetColumn[]; filters: FilterDraft[]; onChange: (value: FilterDraft[]) => void }) {
  const { t } = useI18n();
  const replace = (id: string, next: Partial<FilterDraft>) =>
    onChange(filters.map((item) => (item.id === id ? { ...item, ...next } : item)));
  return (
    <div className="chart-filters">
      {filters.map((filter) => {
        const column = columns.find((item) => item.field_id === filter.fieldId) ?? columns[0];
        const noValue = ["is_null", "is_not_null"].includes(filter.operator);
        return (
          <div className="chart-filter" key={filter.id}>
            <select
              aria-label={t("Filter field")}
              value={filter.fieldId}
              onChange={(event) =>
                onChange(filters.map((item) => (item.id === filter.id ? newFilterDraft(columns.find((c) => c.field_id === event.target.value)!) : item)))
              }
            >
              {columns.map((item) => (
                <option key={item.field_id} value={item.field_id}>{item.label}</option>
              ))}
            </select>
            <select
              aria-label={t("Filter operator")}
              value={filter.operator}
              onChange={(event) => replace(filter.id, { operator: event.target.value as FilterDraft["operator"], value: "", secondValue: "" })}
            >
              {column.filter_operators.map((operator) => (
                <option key={operator} value={operator}>{t(operatorLabels[operator])}</option>
              ))}
            </select>
            {!noValue && (
              <input
                aria-label={t("Filter value")}
                type={filter.operator === "in" ? "text" : inputType(column)}
                value={filter.value}
                placeholder={t(valuePlaceholder(column, filter.operator))}
                onChange={(event) => replace(filter.id, { value: event.target.value })}
              />
            )}
            {filter.operator === "between" && (
              <input
                aria-label={t("Second filter value")}
                type={inputType(column)}
                value={filter.secondValue}
                onChange={(event) => replace(filter.id, { secondValue: event.target.value })}
              />
            )}
            <button className="icon-button" type="button" aria-label={t("Remove filter")} onClick={() => onChange(filters.filter((item) => item.id !== filter.id))}>
              <Trash2 />
            </button>
          </div>
        );
      })}
      {filters.length < 20 && (
        <button className="button ghost small" type="button" onClick={() => onChange([...filters, newFilterDraft(columns[0])])}>
          <Plus /> {t("Add condition")}
        </button>
      )}
    </div>
  );
}

function LivePreview({
  datasetId,
  name,
  definition,
  timezone,
}: {
  datasetId: string;
  name: string;
  definition: ChartDefinition | null;
  timezone: string;
}) {
  const { locale, t } = useI18n();
  const serialized = definition && chartDefinitionIsReady(definition) ? JSON.stringify(definition) : "";
  const [settled, setSettled] = useState(serialized);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(serialized), 350);
    return () => window.clearTimeout(timer);
  }, [serialized]);
  const preview = useQuery({
    queryKey: queryKeys.chartDraftPreview(datasetId, settled),
    queryFn: ({ signal }) => previewChart(datasetId, JSON.parse(settled) as ChartDefinition, signal),
    enabled: Boolean(datasetId && settled),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const result = useMemo<ChartQueryResult | null>(
    () =>
      preview.data
        ? {
            data: preview.data.data,
            meta: {
              ...preview.data.meta,
              chart_id: "",
              chart_name: name || t("Chart"),
              chart_revision: 0,
              last_refresh_error: null,
              last_refresh_error_at: null,
            },
          }
        : null,
    [name, preview.data, t],
  );
  const updating = preview.isFetching || settled !== serialized;
  return (
    <section className="chart-live-preview" aria-label={t("Preview")} aria-busy={updating}>
      <header>
        <strong>{name || t("Chart")}</strong>
        <span>{updating ? <LoaderCircle className="spin" /> : result ? <Check /> : null} {updating ? t("Updating…") : t("Live preview")}</span>
      </header>
      <div className="chart-live-canvas">
        {preview.isError && !updating && <InlineError message={problemMessage(preview.error)} onRetry={() => void preview.refetch()} />}
        {!result && !preview.isError && <div className="chart-live-skeleton skeleton" />}
        {result && result.data.rows.length === 0 && (
          <div className="chart-empty">
            <strong>{t("Nothing to show")}</strong>
            <p>{t("No rows match the conditions.")}</p>
          </div>
        )}
        {result && result.data.rows.length > 0 && (
          <Suspense fallback={<div className="chart-live-skeleton skeleton" />}>
            <ChartView result={result} />
          </Suspense>
        )}
      </div>
      {result && (
        <footer>
          <span>{t("{count} groups", { count: result.data.rows.length })}</span>
          <span>{t("Data from {date}", { date: formatDateTime(result.meta.snapshot_activated_at, locale) })}</span>
          <span>{t("Times in {timezone}", { timezone })}</span>
        </footer>
      )}
      {result?.meta.is_stale && (
        <p className="chart-warning"><AlertTriangle /> {t("The latest load failed, so the previous data is shown.")}</p>
      )}
      {result?.meta.truncated_by_bytes && (
        <p className="chart-warning"><AlertTriangle /> {t("The result is too large and was cut short. Show fewer groups.")}</p>
      )}
    </section>
  );
}

function usePreparedDefinition(
  definition: ChartDefinition | null,
  filters: FilterDraft[],
  exploration?: DatasetExploration,
): { definition: ChartDefinition | null; error: string } {
  const { t } = useI18n();
  return useMemo(() => {
    if (!definition || !exploration) return { definition: null, error: "" };
    try {
      return { definition: definitionWithFilters(definition, filters, exploration.columns), error: "" };
    } catch (error) {
      return { definition: null, error: error instanceof Error ? t(error.message) : t("Check the filter values.") };
    }
  }, [definition, exploration, filters, t]);
}

function safeDefinition(definition: ChartDefinition, filters: FilterDraft[], columns: DatasetColumn[]): ChartDefinition {
  try {
    return definitionWithFilters(definition, filters, columns);
  } catch {
    return definition;
  }
}
