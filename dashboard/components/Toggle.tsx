"use client";

// An iOS-style switch: rounded track, sliding knob, green when on.
export function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-50 ${
        checked ? "bg-green-500" : "bg-neutral-300"
      }`}
    >
      <span
        className={`inline-block h-6 w-6 transform rounded-full bg-white shadow-sm transition-transform duration-200 ${
          checked ? "translate-x-[22px]" : "translate-x-[2px]"
        }`}
      />
    </button>
  );
}
