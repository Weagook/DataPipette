import { AlertCircle, LoaderCircle, RotateCcw } from "lucide-react";

interface FullPageStatusProps {
  label: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function FullPageStatus({
  label,
  detail,
  actionLabel,
  onAction,
}: FullPageStatusProps) {
  return (
    <main className="full-page-status">
      <div className="status-orbit" aria-hidden="true">
        {onAction ? <AlertCircle /> : <LoaderCircle className="spin" />}
      </div>
      <h1>{label}</h1>
      {detail && <p>{detail}</p>}
      {actionLabel && onAction && (
        <button className="button secondary" onClick={onAction} type="button">
          <RotateCcw size={16} />
          {actionLabel}
        </button>
      )}
    </main>
  );
}

export function InlineError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="inline-state error-state" role="alert">
      <AlertCircle size={22} />
      <div>
        <strong>Could not load this view</strong>
        <p>{message}</p>
      </div>
      <button className="icon-button" onClick={onRetry} type="button" aria-label="Retry">
        <RotateCcw size={17} />
      </button>
    </div>
  );
}
