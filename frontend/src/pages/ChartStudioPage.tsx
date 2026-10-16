import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Braces,
  Check,
  ChevronRight,
  Clock3,
  Database,
  Donut,
  Eye,
  Gauge,
  LineChart,
  LoaderCircle,
  PieChart,
  Plus,
  Save,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  Navigate,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  createChart,
  getChart,
  getDatasetExploration,
  listDatasets,
  problemMessage,
  queryChart,
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
  chartTypes,
  createChartDefinition,
  definitionWithFilters,
  filterDraftsFromDefinition,
  isDimensionColumn,
  isTemporalColumn,
  isTimestampColumn,
  newMeasure,
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
import { formatDateTime, relativeTime } from "../lib/format";
import { queryKeys } from "../lib/query";

const ChartView = lazy(() =>
  import("../components/ChartView").then((module) => ({
    default: module.ChartView,
  })),
);

const bucketLabels: Record<ChartTimeBucket, string> = {
  hour: "Hour",
  day: "Day",
  week: "ISO week",
  month: "Month",
  quarter: "Quarter",
  year: "Year",
};

const chartIcons = {
  bar: BarChart3,
  line: LineChart,
  pie: PieChart,
  donut: Donut,
  kpi: Gauge,
};

export function ChartStudioPage() {
  const user = useOutletContext<CurrentUser>();
  const { chartId } = useParams();
  if (user.role === "viewer") return <Navigate to="/overview" replace />;
  return chartId ? (
    <ExistingChartStudio chartId={chartId} />
  ) : (
    <NewChartStudio />
  );
}

function NewChartStudio() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [definition, setDefinition] = useState<ChartDefinition | null>(null);
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [setupError, setSetupError] = useState("");
  const datasets = useQuery({
    queryKey: queryKeys.chartDatasets,
    queryFn: () => listDatasets(),
  });
  const exploration = useQuery({
    queryKey: queryKeys.datasetExploration(datasetId || "none"),
    queryFn: () => getDatasetExploration(datasetId),
    enabled: Boolean(datasetId),
  });

  useEffect(() => {
    if (exploration.data) {
      try {
        setDefinition(createChartDefinition(exploration.data.columns));
        setFilters([]);
        setSetupError("");
      } catch (error) {
        setDefinition(null);
        setSetupError(
          error instanceof Error
            ? error.message
            : "This dataset has no chartable fields.",
        );
      }
    }
  }, [exploration.data]);

  const prepared = usePreparedDefinition(definition, filters, exploration.data);
  const create = useMutation({
    mutationFn: () => {
      if (!prepared.definition)
        throw new Error(prepared.error || "Finish the chart definition.");
      return createChart(name.trim(), datasetId, prepared.definition);
    },
    onSuccess: async ({ chart, etag }) => {
      queryClient.setQueryData(queryKeys.chart(chart.id), { chart, etag });
      await queryClient.invalidateQueries({ queryKey: queryKeys.charts });
      await navigate(`/charts/${chart.id}`, { replace: true });
    },
  });
  const published =
    datasets.data?.data.filter(
      (dataset) => dataset.active_snapshot_id !== null,
    ) ?? [];

  return (
    <div className="chart-studio page-stack">
      <StudioHeading
        eyebrow="New private chart"
        title="Build a visual answer"
        detail="Choose a published dataset. DatePipette saves the analytical definition before it executes a bounded preview."
      />
      <section className="studio-panel chart-source-panel">
        <div className="section-heading">
          <div>
            <span className="step-number">01</span>
            <span>
              <strong>Name and source</strong>
              <small>
                The dataset snapshot stays immutable while you design.
              </small>
            </span>
          </div>
        </div>
        <div className="studio-form-grid">
          <label>
            <span>Chart name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Monthly revenue by region"
              maxLength={100}
            />
          </label>
          <label>
            <span>Published dataset</span>
            <select
              value={datasetId}
              onChange={(event) => {
                setDatasetId(event.target.value);
                setDefinition(null);
                setSetupError("");
              }}
            >
              <option value="">Select a dataset</option>
              {published.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {datasets.isError && (
          <InlineError message={problemMessage(datasets.error)} />
        )}
        {!datasets.isPending && published.length === 0 && (
          <p className="field-note">
            Publish a dataset before creating a chart.
          </p>
        )}
      </section>
      {exploration.isPending && datasetId && (
        <div className="studio-panel skeleton tall" />
      )}
      {exploration.isError && (
        <InlineError message={problemMessage(exploration.error)} />
      )}
      {definition && exploration.data && (
        <ChartBuilder
          definition={definition}
          onDefinitionChange={setDefinition}
          filters={filters}
          onFiltersChange={setFilters}
          exploration={exploration.data}
        />
      )}
      {(setupError || prepared.error || create.isError) && (
        <InlineError
          message={setupError || prepared.error || problemMessage(create.error)}
        />
      )}
      <div className="studio-sticky-actions">
        <span>
          {prepared.definition && chartDefinitionIsReady(prepared.definition)
            ? "Ready to save as a private chart"
            : "Choose a dataset and complete the definition"}
        </span>
        <button
          className="button primary"
          disabled={
            !name.trim() ||
            !prepared.definition ||
            !chartDefinitionIsReady(prepared.definition) ||
            create.isPending
          }
          onClick={() => create.mutate()}
          type="button"
        >
          {create.isPending ? <LoaderCircle className="spin" /> : <Save />}
          Save and preview
        </button>
      </div>
    </div>
  );
}

function ExistingChartStudio({ chartId }: { chartId: string }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<ChartDefinition | null>(null);
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [etag, setEtag] = useState("");
  const initialized = useRef(false);
  const chart = useQuery({
    queryKey: queryKeys.chart(chartId),
    queryFn: () => getChart(chartId),
  });
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
  const canonicalSaved =
    saved && exploration.data
      ? definitionWithSafeFilters(
          saved.definition,
          filterDraftsFromDefinition(saved.definition),
          exploration.data.columns,
        )
      : null;
  const dirty = Boolean(
    saved &&
      prepared.definition &&
      (name !== saved.name ||
        JSON.stringify(prepared.definition) !== JSON.stringify(canonicalSaved)),
  );
  const save = useMutation({
    mutationFn: () => {
      if (!prepared.definition)
        throw new Error(prepared.error || "Finish the chart definition.");
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
  const preview = useQuery({
    queryKey: queryKeys.chartPreview(chartId, saved?.revision ?? 0),
    queryFn: () => queryChart(chartId),
    enabled: Boolean(saved && !dirty),
  });

  if (chart.isPending || (datasetId && exploration.isPending))
    return <div className="dataset-table-skeleton skeleton" />;
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
  if (!saved || !definition || !exploration.data) return null;

  return (
    <div className="chart-studio page-stack">
      <StudioHeading
        eyebrow={`Saved chart · revision ${saved.revision}`}
        title={saved.name}
        detail={`${exploration.data.dataset_name} · active snapshot · ${exploration.data.snapshot_row_count.toLocaleString()} rows`}
      />
      <div className="chart-studio-statusbar">
        <span className={dirty ? "unsaved" : "saved"}>
          {dirty ? <Clock3 /> : <Check />}
          {dirty ? "Unsaved changes" : "Saved definition"}
        </span>
        <span>Private to owner and administrators</span>
        <span>Updated {relativeTime(saved.updated_at)}</span>
      </div>
      <section className="studio-panel">
        <div className="studio-form-grid single-name">
          <label>
            <span>Chart name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={100}
            />
          </label>
          <div className="source-lock">
            <Database />
            <span>
              <strong>{exploration.data.dataset_name}</strong>
              <small>Published dataset · source locked</small>
            </span>
          </div>
        </div>
      </section>
      <div className="chart-studio-workspace">
        <div className="chart-config-column">
          <ChartBuilder
            definition={definition}
            onDefinitionChange={setDefinition}
            filters={filters}
            onFiltersChange={setFilters}
            exploration={exploration.data}
          />
        </div>
        <PreviewPanel
          result={preview.data}
          isPending={preview.isPending && !dirty}
          error={preview.error}
          dirty={dirty}
          onRetry={() => void preview.refetch()}
        />
      </div>
      {(prepared.error || save.isError) && (
        <InlineError message={prepared.error || problemMessage(save.error)} />
      )}
      <div className="studio-sticky-actions">
        <span>
          {dirty
            ? "Save before the preview can execute this revision"
            : `Preview uses saved revision ${saved.revision}`}
        </span>
        <button
          className="button primary"
          disabled={
            !dirty ||
            !name.trim() ||
            !prepared.definition ||
            !chartDefinitionIsReady(prepared.definition) ||
            save.isPending
          }
          onClick={() => save.mutate()}
          type="button"
        >
          {save.isPending ? <LoaderCircle className="spin" /> : <Save />}
          Save revision
        </button>
      </div>
    </div>
  );
}

function ChartBuilder({
  definition,
  onDefinitionChange,
  filters,
  onFiltersChange,
  exploration,
}: {
  definition: ChartDefinition;
  onDefinitionChange: (value: ChartDefinition) => void;
  filters: FilterDraft[];
  onFiltersChange: (value: FilterDraft[]) => void;
  exploration: DatasetExploration;
}) {
  const dimension = definition.dimensions?.[0];
  const dimensionColumn = exploration.columns.find(
    (column) => column.field_id === dimension?.field_id,
  );
  const multipleMeasures = ["bar", "line"].includes(definition.chart_type);

  return (
    <>
      <section className="studio-panel chart-type-panel">
        <div className="section-heading">
          <div>
            <span className="step-number">02</span>
            <span>
              <strong>Visual form</strong>
              <small>Choose the clearest shape for the question.</small>
            </span>
          </div>
        </div>
        <div
          className="chart-type-grid"
          role="radiogroup"
          aria-label="Chart type"
        >
          {chartTypes.map((item) => {
            const Icon = chartIcons[item.value];
            const selected = definition.chart_type === item.value;
            return (
              <button
                className={selected ? "selected" : ""}
                key={item.value}
                onClick={() =>
                  onDefinitionChange(
                    changeChartType(
                      definition,
                      item.value,
                      exploration.columns,
                    ),
                  )
                }
                type="button"
                aria-pressed={selected}
              >
                <Icon />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.hint}</small>
                </span>
                {selected && <Check />}
              </button>
            );
          })}
        </div>
      </section>

      {definition.chart_type !== "kpi" && (
        <section className="studio-panel">
          <div className="section-heading">
            <div>
              <span className="step-number">03</span>
              <span>
                <strong>Dimension</strong>
                <small>One field defines the groups on the visual axis.</small>
              </span>
            </div>
          </div>
          <div className="dimension-grid">
            <label>
              <span>Group by</span>
              <select
                value={dimension?.field_id ?? ""}
                onChange={(event) =>
                  onDefinitionChange({
                    ...definition,
                    dimensions: [
                      { field_id: event.target.value, time_bucket: null },
                    ],
                    sorts: [],
                  })
                }
              >
                {exploration.columns.filter(isDimensionColumn).map((column) => (
                  <option key={column.field_id} value={column.field_id}>
                    {column.label} · {column.data_type}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Time bucket</span>
              <select
                value={dimension?.time_bucket ?? ""}
                disabled={
                  !dimensionColumn || !isTemporalColumn(dimensionColumn)
                }
                onChange={(event) =>
                  onDefinitionChange({
                    ...definition,
                    dimensions: [
                      {
                        field_id: dimension!.field_id,
                        time_bucket:
                          (event.target.value as ChartTimeBucket) || null,
                      },
                    ],
                  })
                }
              >
                <option value="">Exact values</option>
                {Object.entries(bucketLabels)
                  .filter(
                    ([value]) =>
                      value !== "hour" ||
                      (dimensionColumn && isTimestampColumn(dimensionColumn)),
                  )
                  .map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {dimensionColumn && isTemporalColumn(dimensionColumn) && (
            <p className="field-note">
              Timestamp buckets use {exploration.installation_timezone}; dates
              keep their calendar value.
            </p>
          )}
        </section>
      )}

      <section className="studio-panel">
        <div className="section-heading">
          <div>
            <span className="step-number">
              {definition.chart_type === "kpi" ? "03" : "04"}
            </span>
            <span>
              <strong>Measures</strong>
              <small>
                Aggregate published fields into stable result columns.
              </small>
            </span>
          </div>
          {multipleMeasures && definition.measures.length < 5 && (
            <button
              className="button secondary small"
              onClick={() =>
                onDefinitionChange({
                  ...definition,
                  measures: [
                    ...definition.measures,
                    newMeasure(
                      exploration.columns.find(
                        (column) => aggregationsFor(column).length > 0,
                      )!,
                    ),
                  ],
                  sorts: [],
                })
              }
              type="button"
            >
              <Plus /> Add measure
            </button>
          )}
        </div>
        <div className="measure-list">
          {definition.measures.map((measure, index) => {
            const column =
              exploration.columns.find(
                (item) => item.field_id === measure.field_id,
              ) ?? exploration.columns[0];
            const allowed = aggregationsFor(column);
            return (
              <div className="measure-row" key={measure.id}>
                <span className="measure-index">M{index + 1}</span>
                <label>
                  <span>Field</span>
                  <select
                    value={measure.field_id}
                    onChange={(event) => {
                      const nextColumn = exploration.columns.find(
                        (item) => item.field_id === event.target.value,
                      )!;
                      const aggregation = aggregationsFor(nextColumn)[0];
                      onDefinitionChange({
                        ...definition,
                        measures: definition.measures.map((item) =>
                          item.id === measure.id
                            ? {
                                ...item,
                                field_id: nextColumn.field_id,
                                aggregation,
                                label: `${aggregationLabels[aggregation]} of ${nextColumn.label}`,
                              }
                            : item,
                        ),
                        sorts: [],
                      });
                    }}
                  >
                    {exploration.columns
                      .filter((item) => aggregationsFor(item).length > 0)
                      .map((item) => (
                        <option key={item.field_id} value={item.field_id}>
                          {item.label} · {item.data_type}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  <span>Aggregation</span>
                  <select
                    value={measure.aggregation}
                    onChange={(event) =>
                      onDefinitionChange({
                        ...definition,
                        measures: definition.measures.map((item) =>
                          item.id === measure.id
                            ? {
                                ...item,
                                aggregation: event.target
                                  .value as typeof measure.aggregation,
                              }
                            : item,
                        ),
                      })
                    }
                  >
                    {allowed.map((aggregation) => (
                      <option key={aggregation} value={aggregation}>
                        {aggregationLabels[aggregation]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Result label</span>
                  <input
                    value={measure.label}
                    maxLength={100}
                    onChange={(event) =>
                      onDefinitionChange({
                        ...definition,
                        measures: definition.measures.map((item) =>
                          item.id === measure.id
                            ? { ...item, label: event.target.value }
                            : item,
                        ),
                      })
                    }
                  />
                </label>
                {multipleMeasures && definition.measures.length > 1 && (
                  <button
                    className="icon-button"
                    aria-label={`Remove ${measure.label}`}
                    onClick={() =>
                      onDefinitionChange({
                        ...definition,
                        measures: definition.measures.filter(
                          (item) => item.id !== measure.id,
                        ),
                        sorts: (definition.sorts ?? []).filter(
                          (sort) => sort.target_id !== measure.id,
                        ),
                      })
                    }
                    type="button"
                  >
                    <Trash2 />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <FilterSection
        columns={exploration.columns}
        filters={filters}
        onChange={onFiltersChange}
      />
      <SortAndLimit
        definition={definition}
        onChange={onDefinitionChange}
        columns={exploration.columns}
      />
      <details className="studio-panel json-inspector">
        <summary>
          <Braces /> Definition JSON <ChevronRight />
        </summary>
        <pre>
          {JSON.stringify(
            definitionWithSafeFilters(definition, filters, exploration.columns),
            null,
            2,
          )}
        </pre>
      </details>
    </>
  );
}

function FilterSection({
  columns,
  filters,
  onChange,
}: {
  columns: DatasetColumn[];
  filters: FilterDraft[];
  onChange: (value: FilterDraft[]) => void;
}) {
  return (
    <section className="studio-panel">
      <div className="section-heading">
        <div>
          <SlidersHorizontal className="section-icon" />
          <span>
            <strong>Persistent filters</strong>
            <small>These conditions become part of the saved chart.</small>
          </span>
        </div>
        {filters.length < 20 && (
          <button
            className="button secondary small"
            onClick={() => onChange([...filters, newFilterDraft(columns[0])])}
            type="button"
          >
            <Plus /> Add filter
          </button>
        )}
      </div>
      {filters.length === 0 ? (
        <div className="compact-empty">
          <SlidersHorizontal />
          <span>No permanent filters. Every snapshot row contributes.</span>
        </div>
      ) : (
        <div className="chart-filter-list">
          {filters.map((filter) => {
            const column =
              columns.find((item) => item.field_id === filter.fieldId) ??
              columns[0];
            const noValue = ["is_null", "is_not_null"].includes(
              filter.operator,
            );
            return (
              <div className="chart-filter-row" key={filter.id}>
                <select
                  aria-label="Filter field"
                  value={filter.fieldId}
                  onChange={(event) => {
                    const next = columns.find(
                      (item) => item.field_id === event.target.value,
                    )!;
                    onChange(
                      filters.map((item) =>
                        item.id === filter.id ? newFilterDraft(next) : item,
                      ),
                    );
                  }}
                >
                  {columns.map((item) => (
                    <option key={item.field_id} value={item.field_id}>
                      {item.label}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Filter operator"
                  value={filter.operator}
                  onChange={(event) =>
                    onChange(
                      filters.map((item) =>
                        item.id === filter.id
                          ? {
                              ...item,
                              operator: event.target
                                .value as FilterDraft["operator"],
                              value: "",
                              secondValue: "",
                            }
                          : item,
                      ),
                    )
                  }
                >
                  {column.filter_operators.map((operator) => (
                    <option key={operator} value={operator}>
                      {operatorLabels[operator]}
                    </option>
                  ))}
                </select>
                {!noValue && (
                  <input
                    aria-label="Filter value"
                    type={filter.operator === "in" ? "text" : inputType(column)}
                    value={filter.value}
                    placeholder={valuePlaceholder(column, filter.operator)}
                    onChange={(event) =>
                      onChange(
                        filters.map((item) =>
                          item.id === filter.id
                            ? { ...item, value: event.target.value }
                            : item,
                        ),
                      )
                    }
                  />
                )}
                {filter.operator === "between" && (
                  <input
                    aria-label="Second filter value"
                    type={inputType(column)}
                    value={filter.secondValue}
                    onChange={(event) =>
                      onChange(
                        filters.map((item) =>
                          item.id === filter.id
                            ? { ...item, secondValue: event.target.value }
                            : item,
                        ),
                      )
                    }
                  />
                )}
                <button
                  className="icon-button"
                  aria-label="Remove filter"
                  onClick={() =>
                    onChange(filters.filter((item) => item.id !== filter.id))
                  }
                  type="button"
                >
                  <Trash2 />
                </button>
              </div>
            );
          })}
          <p className="field-note">
            Separate multiple “is any of” values with semicolons.
          </p>
        </div>
      )}
    </section>
  );
}

function SortAndLimit({
  definition,
  onChange,
  columns,
}: {
  definition: ChartDefinition;
  onChange: (value: ChartDefinition) => void;
  columns: DatasetColumn[];
}) {
  const dimension = definition.dimensions?.[0];
  const dimensionColumn = columns.find(
    (item) => item.field_id === dimension?.field_id,
  );
  const sort = definition.sorts?.[0];
  const sortValue = sort ? `${sort.target_type}:${sort.target_id}` : "";
  return (
    <section className="studio-panel compact-controls">
      <div className="section-heading">
        <div>
          <span className="step-number">↕</span>
          <span>
            <strong>Order and result size</strong>
            <small>Keep the response focused and deterministic.</small>
          </span>
        </div>
      </div>
      <div className="sort-limit-grid">
        <label>
          <span>Sort result by</span>
          <select
            value={sortValue}
            onChange={(event) => {
              if (!event.target.value) {
                onChange({ ...definition, sorts: [] });
                return;
              }
              const [targetType, targetId] = event.target.value.split(":");
              onChange({
                ...definition,
                sorts: [
                  {
                    target_type: targetType as "dimension" | "measure",
                    target_id: targetId,
                    direction: sort?.direction ?? "desc",
                  },
                ],
              });
            }}
          >
            <option value="">Natural order</option>
            {dimension && (
              <option value={`dimension:${dimension.field_id}`}>
                Dimension · {dimensionColumn?.label}
              </option>
            )}
            {definition.measures.map((measure) => (
              <option key={measure.id} value={`measure:${measure.id}`}>
                Measure · {measure.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Direction</span>
          <select
            value={sort?.direction ?? "desc"}
            disabled={!sort}
            onChange={(event) =>
              onChange({
                ...definition,
                sorts: sort
                  ? [
                      {
                        ...sort,
                        direction: event.target.value as "asc" | "desc",
                      },
                    ]
                  : [],
              })
            }
          >
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
          </select>
        </label>
        <label>
          <span>Maximum groups</span>
          <input
            type="number"
            min={1}
            max={1000}
            value={definition.limit}
            onChange={(event) =>
              onChange({ ...definition, limit: Number(event.target.value) })
            }
          />
        </label>
      </div>
    </section>
  );
}

function PreviewPanel({
  result,
  isPending,
  error,
  dirty,
  onRetry,
}: {
  result?: ChartQueryResult;
  isPending: boolean;
  error: unknown;
  dirty: boolean;
  onRetry: () => void;
}) {
  return (
    <aside className="chart-preview-panel">
      <header>
        <div>
          <span className="eyebrow">Saved preview</span>
          <h2>{result?.meta.chart_name ?? "Visualization"}</h2>
        </div>
        <span className="bounded-pill">Bounded query</span>
      </header>
      <div className="chart-preview-canvas">
        {dirty && (
          <div className="preview-message">
            <Save />
            <strong>Save to refresh preview</strong>
            <p>Unsaved definitions are never executed against snapshot data.</p>
          </div>
        )}
        {!dirty && isPending && (
          <div className="chart-skeleton">
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
        )}
        {!dirty && Boolean(error) && (
          <InlineError message={problemMessage(error)} onRetry={onRetry} />
        )}
        {!dirty && result && result.data.rows.length === 0 && (
          <div className="preview-message">
            <Eye />
            <strong>No matching groups</strong>
            <p>The saved filters returned no aggregate rows.</p>
          </div>
        )}
        {!dirty && result && result.data.rows.length > 0 && (
          <Suspense
            fallback={
              <div className="chart-skeleton">
                <i />
                <i />
                <i />
              </div>
            }
          >
            <ChartView result={result} />
          </Suspense>
        )}
      </div>
      {result && !dirty && (
        <footer>
          <span>
            <Check /> revision {result.meta.chart_revision}
          </span>
          <span>{result.data.rows.length} groups</span>
          <span>{formatDateTime(result.meta.snapshot_activated_at)}</span>
        </footer>
      )}
      {result?.meta.is_stale && !dirty && (
        <div className="preview-warning">
          <AlertTriangle />
          <span>
            Showing the last successful snapshot.{" "}
            {result.meta.last_refresh_error}
          </span>
        </div>
      )}
      {result?.meta.truncated_by_bytes && !dirty && (
        <div className="preview-warning">
          <AlertTriangle /> Response reached the configured byte limit.
        </div>
      )}
    </aside>
  );
}

function StudioHeading({
  eyebrow,
  title,
  detail,
}: {
  eyebrow: string;
  title: string;
  detail: string;
}) {
  return (
    <header className="page-heading chart-studio-heading">
      <Link className="back-link" to="/charts">
        <ArrowLeft /> Charts
      </Link>
      <span className="eyebrow">{eyebrow}</span>
      <h1>{title}</h1>
      <p>{detail}</p>
    </header>
  );
}

function usePreparedDefinition(
  definition: ChartDefinition | null,
  filters: FilterDraft[],
  exploration?: DatasetExploration,
): { definition: ChartDefinition | null; error: string } {
  return useMemo(() => {
    if (!definition || !exploration) return { definition: null, error: "" };
    try {
      return {
        definition: definitionWithFilters(
          definition,
          filters,
          exploration.columns,
        ),
        error: "",
      };
    } catch (error) {
      return {
        definition: null,
        error:
          error instanceof Error ? error.message : "Check the filter values.",
      };
    }
  }, [definition, exploration, filters]);
}

function definitionWithSafeFilters(
  definition: ChartDefinition,
  filters: FilterDraft[],
  columns: DatasetColumn[],
): ChartDefinition {
  try {
    return definitionWithFilters(definition, filters, columns);
  } catch {
    return definition;
  }
}
