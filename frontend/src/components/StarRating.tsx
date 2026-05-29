import { useState } from "react";

interface Props {
  value?: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  size?: "sm" | "md";
  label?: string;
}

/** 1-5 star rating widget. Hover shows tentative selection. */
export function StarRating({
  value = 0,
  onChange,
  disabled,
  size = "md",
  label,
}: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = hover ?? value;
  const px = size === "sm" ? "text-base" : "text-xl";

  return (
    <div className="flex items-center gap-2">
      {label && <span className="label">{label}</span>}
      <div
        className="flex items-center gap-0.5"
        role="radiogroup"
        onMouseLeave={() => setHover(null)}
      >
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            aria-label={`${n} star${n > 1 ? "s" : ""}`}
            disabled={disabled}
            onMouseEnter={() => setHover(n)}
            onClick={() => onChange(n)}
            className={
              "leading-none transition-transform " +
              px +
              (disabled
                ? " text-ink-300 cursor-not-allowed"
                : " hover:scale-110 " +
                  (shown >= n ? "text-amber-400" : "text-ink-200"))
            }
          >
            {shown >= n ? "★" : "☆"}
          </button>
        ))}
      </div>
    </div>
  );
}
