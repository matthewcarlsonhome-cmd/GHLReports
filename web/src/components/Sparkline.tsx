// 8-week leads sparkline for portfolio rows: a single 2px series line on a
// transparent ground, no axes, endpoint dot. Gate-held days are gaps.

export function Sparkline({
  values,
  width = 96,
  height = 24,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
}) {
  const numeric = values.filter((v): v is number => v !== null);
  if (numeric.length < 2) {
    return <span className="text-xxs text-muted">—</span>;
  }
  const max = Math.max(...numeric, 1);
  const min = Math.min(...numeric, 0);
  const range = max - min || 1;
  const pad = 2;
  const step = (width - pad * 2) / (values.length - 1);
  const points: { x: number; y: number }[] = [];
  values.forEach((value, i) => {
    if (value === null) return;
    points.push({
      x: pad + i * step,
      y: pad + (height - pad * 2) * (1 - (value - min) / range),
    });
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const title = `last 8 weeks: ${numeric.join(", ")}`;
  return (
    <svg width={width} height={height} role="img" aria-label={title}>
      <title>{title}</title>
      <path d={path} fill="none" stroke="#2a78d6" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last.x} cy={last.y} r={2.5} fill="#2a78d6" />
    </svg>
  );
}
