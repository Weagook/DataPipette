import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, FileSpreadsheet, LoaderCircle, XCircle } from "lucide-react";
import { useState } from "react";

import {
  createDatasetExport,
  issueExportDownload,
  listDatasetExports,
  problemMessage,
  type ExportFormat,
  type SnapshotRowsQuery,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { queryKeys } from "../lib/query";

interface ExportPanelProps {
  datasetId: string;
  query: Pick<SnapshotRowsQuery, "fields" | "filters" | "sorts">;
}

export function ExportPanel({ datasetId, query }: ExportPanelProps) {
  const [open, setOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const exportsQuery = useQuery({
    queryKey: queryKeys.datasetExports(datasetId),
    queryFn: () => listDatasetExports(datasetId),
    enabled: open,
    refetchInterval: (result) =>
      result.state.data?.data.some((job) => job.state === "queued" || job.state === "running")
        ? 3_000
        : false,
  });
  const createExport = useMutation({
    mutationFn: (format: ExportFormat) => createDatasetExport(datasetId, format, query),
    onSuccess: async () => {
      setFeedback("Export queued. You can keep working while it is prepared.");
      await queryClient.invalidateQueries({ queryKey: queryKeys.datasetExports(datasetId) });
    },
    onError: (error) => setFeedback(problemMessage(error)),
  });
  const download = useMutation({
    mutationFn: (exportId: string) => issueExportDownload(datasetId, exportId),
    onSuccess: ({ download_url }) => {
      const anchor = document.createElement("a");
      anchor.href = download_url;
      anchor.rel = "noopener";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setFeedback("A fresh one-time download link was issued.");
    },
    onError: (error) => setFeedback(problemMessage(error)),
  });

  return (
    <div className="export-wrap">
      <button className="button secondary" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <Download size={16} /> Export
      </button>
      {open && (
        <aside className="export-panel" aria-label="Exports">
          <header><div><span className="eyebrow">Current table</span><h2>Exports</h2></div><button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="Close exports"><XCircle size={19} /></button></header>
          <p>Each file uses the active columns, filters, sort order, and snapshot.</p>
          <div className="export-actions">
            {(["csv", "xlsx"] as const).map((format) => (
              <button className="button secondary" type="button" key={format} disabled={createExport.isPending} onClick={() => createExport.mutate(format)}>
                <FileSpreadsheet size={16} /> {format.toUpperCase()}
              </button>
            ))}
          </div>
          {feedback && <p className="export-feedback" role="status">{feedback}</p>}
          <div className="export-history">
            {exportsQuery.isPending && <p>Loading export history…</p>}
            {exportsQuery.isError && <p className="filter-error">{problemMessage(exportsQuery.error)}</p>}
            {exportsQuery.data?.data.map((job) => {
              const expired = new Date(job.expires_at).getTime() <= Date.now();
              return (
                <article key={job.id}>
                  <span className={`export-state ${job.state}`}>
                    {job.state === "succeeded" ? <Check size={13} /> : job.state === "failed" ? <XCircle size={13} /> : <LoaderCircle className="spin" size={13} />}
                    {expired && job.state === "succeeded" ? "expired" : job.state}
                  </span>
                  <div><strong>{job.format.toUpperCase()}</strong><small>{formatDateTime(job.requested_at)} · {job.row_count == null ? "Preparing" : `${job.row_count.toLocaleString()} rows`}</small></div>
                  {job.state === "succeeded" && !expired && (
                    <button className="button secondary small" type="button" disabled={download.isPending} onClick={() => download.mutate(job.id)}>Download</button>
                  )}
                </article>
              );
            })}
            {!exportsQuery.isPending && exportsQuery.data?.data.length === 0 && <p>No exports yet.</p>}
          </div>
        </aside>
      )}
    </div>
  );
}
