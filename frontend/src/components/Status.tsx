import { AlertCircle, LoaderCircle, RotateCcw } from "lucide-react";

import { useI18n } from "../lib/i18n";

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
    <main className="full-page-status" aria-busy={!onAction}>
      <section
        className="full-page-status-message"
        role={onAction ? "alert" : "status"}
        aria-live={onAction ? "assertive" : "polite"}
      >
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
      </section>
    </main>
  );
}

export function InlineError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="inline-state error-state" role="alert">
      <AlertCircle size={22} />
      <div>
        <strong>{t("Could not load this view")}</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button
          className="icon-button"
          onClick={onRetry}
          type="button"
          aria-label={t("Retry")}
        >
          <RotateCcw size={17} />
        </button>
      )}
    </div>
  );
}
