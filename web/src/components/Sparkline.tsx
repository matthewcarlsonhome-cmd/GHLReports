// Sparkline.tsx — a hand-rolled SVG mini line chart (no charting library).
//
// 8-week leads sparkline for portfolio rows: a single 2px series line on a
// transparent ground, no axes, endpoint dot. Gate-held days are gaps.
//
// How it works: each value maps to an (x, y) point inside a width×height SVG.
// x spreads the points evenly across the width; y is the value scaled between
// the min and max of the series (so every sparkline uses its own scale —
// they show *shape*, not comparable magnitudes). The points become one SVG
// <path> string, "M x,y" (move to) followed by "L x,y" (line to) commands.

// Props: `values` is oldest-first; null entries are weeks with no trusted data
// and are simply skipped, leaving a straight segment across the gap.
export function Sparkline({
  values,
  width = 96,
  height = 24,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
}) {
  // Type-guard filter: tells TypeScript the survivors are plain numbers.
  const numeric = values.filter((v): v is number => v !== null);
  if (numeric.length < 2) {
    // Can't draw a line through fewer than 2 points — show a dash instead.
    return <span className="text-xxs text-muted">—</span>;
  }
  // Anchor the scale: max at least 1 and min at most 0 so a flat series
  // doesn't stretch into drama, and `|| 1` avoids dividing by zero.
  const max = Math.max(...numeric, 1);
  const min = Math.min(...numeric, 0);
  const range = max - min || 1;
  const pad = 2; // keeps the 2px stroke from clipping at the SVG edges
  const step = (width - pad * 2) / (values.length - 1);
  const points: { x: number; y: number }[] = [];
  values.forEach((value, i) => {
    if (value === null) return;
    points.push({
      x: pad + i * step,
      // SVG y grows downward, so invert: bigger value -> smaller y.
      y: pad + (height - pad * 2) * (1 - (value - min) / range),
    });
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  // role="img" + aria-label + <title> give screen readers and mouse hover the
  // raw numbers the picture encodes.
  const title = `last 8 weeks: ${numeric.join(", ")}`;
  return (
    <svg width={width} height={height} role="img" aria-label={title}>
      <title>{title}</title>
      <path d={path} fill="none" stroke="#2a78d6" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {/* endpoint dot marks "now" so the reading direction is obvious */}
      <circle cx={last.x} cy={last.y} r={2.5} fill="#2a78d6" />
    </svg>
  );
}
