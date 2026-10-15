interface BrandProps {
  compact?: boolean;
}

export function Brand({ compact = false }: BrandProps) {
  return (
    <div className="brand" aria-label="DatePipette">
      <span className="brand-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {!compact && (
        <span className="brand-name">
          Date<span>Pipette</span>
        </span>
      )}
    </div>
  );
}
