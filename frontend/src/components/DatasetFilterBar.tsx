import { Filter, Plus, Trash2 } from "lucide-react";

import type { DatasetColumn } from "../lib/api";
import {
  inputType,
  newFilterDraft,
  operatorLabels,
  valuePlaceholder,
  type FilterDraft,
  type FilterOperator,
} from "../lib/datasetQuery";

interface DatasetFilterBarProps {
  columns: DatasetColumn[];
  drafts: FilterDraft[];
  error: string | null;
  onChange: (drafts: FilterDraft[]) => void;
  onApply: () => void;
  onClear: () => void;
}

export function DatasetFilterBar({
  columns,
  drafts,
  error,
  onChange,
  onApply,
  onClear,
}: DatasetFilterBarProps) {
  const filterable = columns.filter((column) => column.filter_operators.length > 0);

  function updateDraft(id: string, change: Partial<FilterDraft>) {
    onChange(drafts.map((draft) => (draft.id === id ? { ...draft, ...change } : draft)));
  }

  return (
    <section className="filter-panel" aria-label="Dataset filters">
      <header>
        <div>
          <span className="panel-icon"><Filter size={16} /></span>
          <div><strong>Filters</strong><small>Draft changes apply only when you confirm them.</small></div>
        </div>
        <button
          className="button secondary small"
          type="button"
          disabled={filterable.length === 0}
          onClick={() => onChange([...drafts, newFilterDraft(filterable[0])])}
        >
          <Plus size={15} /> Add filter
        </button>
      </header>
      {drafts.length === 0 ? (
        <p className="filter-hint">No filters applied. Add one to narrow the current snapshot.</p>
      ) : (
        <div className="filter-rows">
          {drafts.map((draft) => {
            const column = columns.find((candidate) => candidate.field_id === draft.fieldId) ?? filterable[0];
            const noValue = draft.operator === "is_null" || draft.operator === "is_not_null";
            return (
              <div className="filter-row" key={draft.id}>
                <label>
                  <span>Column</span>
                  <select
                    aria-label="Filter column"
                    value={draft.fieldId}
                    onChange={(event) => {
                      const nextColumn = columns.find((candidate) => candidate.field_id === event.target.value);
                      if (nextColumn) {
                        updateDraft(draft.id, {
                          fieldId: nextColumn.field_id,
                          operator: nextColumn.filter_operators[0] ?? "eq",
                          value: "",
                          secondValue: "",
                        });
                      }
                    }}
                  >
                    {filterable.map((candidate) => <option key={candidate.field_id} value={candidate.field_id}>{candidate.label}</option>)}
                  </select>
                </label>
                <label>
                  <span>Condition</span>
                  <select
                    aria-label="Filter condition"
                    value={draft.operator}
                    onChange={(event) => updateDraft(draft.id, { operator: event.target.value as FilterOperator })}
                  >
                    {column.filter_operators.map((operator) => <option key={operator} value={operator}>{operatorLabels[operator]}</option>)}
                  </select>
                </label>
                {!noValue && (
                  <label className="filter-value">
                    <span>{draft.operator === "in" ? "Values separated by ;" : "Value"}</span>
                    <input
                      aria-label="Filter value"
                      type={draft.operator === "in" ? "text" : inputType(column)}
                      value={draft.value}
                      step="any"
                      placeholder={valuePlaceholder(column, draft.operator)}
                      onChange={(event) => updateDraft(draft.id, { value: event.target.value })}
                    />
                  </label>
                )}
                {draft.operator === "between" && (
                  <label className="filter-value">
                    <span>And</span>
                    <input
                      aria-label="Second filter value"
                      type={inputType(column)}
                      value={draft.secondValue}
                      step="any"
                      placeholder="End of range"
                      onChange={(event) => updateDraft(draft.id, { secondValue: event.target.value })}
                    />
                  </label>
                )}
                <button
                  className="icon-button bordered filter-remove"
                  type="button"
                  aria-label={`Remove ${column.label} filter`}
                  onClick={() => onChange(drafts.filter((candidate) => candidate.id !== draft.id))}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            );
          })}
        </div>
      )}
      {error && <p className="filter-error" role="alert">{error}</p>}
      <footer>
        <button className="button secondary small" type="button" onClick={onClear} disabled={drafts.length === 0}>Clear all</button>
        <button className="button primary small" type="button" onClick={onApply}>Apply filters</button>
      </footer>
    </section>
  );
}
