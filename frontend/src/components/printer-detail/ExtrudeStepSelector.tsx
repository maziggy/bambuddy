const EXTRUDE_STEPS = [10, 50, 100] as const;
export type ExtrudeStep = (typeof EXTRUDE_STEPS)[number];

interface ExtrudeStepSelectorProps {
  value: ExtrudeStep;
  onChange: (step: ExtrudeStep) => void;
  label: string;
  disabled?: boolean;
}

export function ExtrudeStepSelector({ value, onChange, label, disabled }: ExtrudeStepSelectorProps) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-bambu-gray/70 mb-1">{label}</div>
      <div className="flex gap-1">
        {EXTRUDE_STEPS.map((step) => (
          <button
            key={step}
            type="button"
            disabled={disabled}
            onClick={() => onChange(step)}
            className={`flex-1 px-2 py-1 rounded text-xs transition-colors ${
              value === step
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'bg-bambu-dark text-bambu-gray hover:bg-bambu-dark-tertiary disabled:opacity-50'
            }`}
          >
            {step}
          </button>
        ))}
      </div>
    </div>
  );
}
