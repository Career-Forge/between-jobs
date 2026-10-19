import { useId, type ReactNode } from "react";
import { defaultSegments, filledSegments, hatchedSegments } from "../../lib/segments";
import "../../styles/paper-doc.css";

// A score part as a row of cells: label on the left, the value in words on the right, the cells under
// them. Never a gauge, ring, radar or bare number.
//
//   <SegmentedBar label="Semantic coverage" value={27} max={35} valueText="27 / 35 · near max"
//                 detail="Eight of eleven skill clusters are covered." />
//
// One cell per point up to 35 (the ATS rows), otherwise twenty (a /100 sub-score); `segments` chooses.
// A filled cell is solid gold and an empty one is a hollow outline, so the bar reads without color;
// the value is always also in words. `hatched` is exact headroom that is not earned yet (a management
// credit the engine can name exactly): hatched gold cells after the filled ones, never solid.
// lib/segments.ts is the arithmetic: a value between 0 and the maximum never draws an empty bar or a
// full one, and with one cell per point the filled count is the value.
//
// Its name and value are one `meter` for assistive tech: the label, the value as words
// (aria-valuetext) and the number out of the maximum. With no value (`null`: not scored, a placeholder)
// the cells are all hollow, there is no number to give, and it is an image named by the label and the
// words, so a bar is never read as zero.

export interface SegmentedBarProps {
  label: string;
  /** null when there is nothing to draw: the bar is hollow and `valueText` says why. */
  value: number | null;
  max: number;
  /** Cells in the bar. Defaults to one per point up to 35, else twenty. */
  segments?: number;
  /** The words at the right: "27 / 35 · near max". Defaults to "27 / 35", or "Not scored" with no value. */
  valueText?: string;
  /** Exact headroom in points that is not earned yet, drawn hatched after the filled cells. */
  hatched?: number | null;
  /** A caption under the bar. */
  detail?: ReactNode;
  className?: string;
}

function wholeOrDecimal(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 10) / 10);
}

export function SegmentedBar({ label, value, max, segments, valueText, hatched, detail, className }: SegmentedBarProps) {
  const labelId = useId();
  const cells = segments ?? defaultSegments(max);
  const known = typeof value === "number" && Number.isFinite(value) && Number.isFinite(max) && max > 0;
  const filled = known ? filledSegments(value, max, cells) : 0;
  const headroom = known ? hatchedSegments(value, hatched, max, cells) : 0;
  const words = valueText ?? (known ? `${wholeOrDecimal(value)} / ${wholeOrDecimal(max)}` : "Not scored");

  const row = (
    <>
      <span id={labelId} className="bj-segbar-label">
        {label}
      </span>
      <span className="bj-segbar-value">{words}</span>
      <span className="bj-segbar-cells" aria-hidden="true">
        {Array.from({ length: Math.max(0, cells) }, (_, index) => (
          <span
            key={index}
            className={index < filled ? "bj-segbar-cell bj-segbar-cell--filled" : index < filled + headroom ? "bj-segbar-cell bj-segbar-cell--hatched" : "bj-segbar-cell"}
          />
        ))}
      </span>
    </>
  );

  return (
    <div className={className === undefined ? "bj-segbar" : `bj-segbar ${className}`}>
      {known ? (
        <div
          className="bj-segbar-row"
          role="meter"
          aria-labelledby={labelId}
          aria-valuemin={0}
          aria-valuemax={max}
          aria-valuenow={Math.min(Math.max(value, 0), max)}
          aria-valuetext={words}
        >
          {row}
        </div>
      ) : (
        <div className="bj-segbar-row" role="img" aria-label={`${label}: ${words}`}>
          {row}
        </div>
      )}
      {detail !== undefined && detail !== null && <div className="bj-segbar-detail">{detail}</div>}
    </div>
  );
}
