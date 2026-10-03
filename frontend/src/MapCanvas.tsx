import { useMemo, useState } from "react";
import type { ClosureZone, ForbiddenZone, MapState, Point, Project, ValidationResult } from "./types";

type Mode = "zone" | "closure" | "start" | "end";

type Props = {
  project: Project;
  state: MapState;
  scale: number;
  result: ValidationResult | null;
  onChange: (state: MapState) => void;
};

export function MapCanvas({ project, state, scale, result, onChange }: Props) {
  const [mode, setMode] = useState<Mode>("zone");
  const [draftPoint, setDraftPoint] = useState<Point | null>(null);
  const mapWidth = project.width * project.resolution;
  const mapHeight = project.height * project.resolution;
  const origin = state.origin;

  const wallRects = useMemo(() => {
    const rects: Array<{ x: number; y: number }> = [];
    project.grid.forEach((row, rowIndex) =>
      row.forEach((blocked, colIndex) => {
        if (blocked) {
          rects.push({
            x: origin.x + colIndex * project.resolution,
            y: origin.y + (project.height - 1 - rowIndex) * project.resolution,
          });
        }
      })
    );
    return rects;
  }, [origin.x, origin.y, project.grid, project.height, project.resolution]);

  const pathPoints = result?.path ?? [];
  const pathD = pathPoints
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");

  function svgPoint(event: React.MouseEvent<SVGSVGElement>): Point {
    const svg = event.currentTarget;
    const box = svg.getBoundingClientRect();
    const x = origin.x + ((event.clientX - box.left) / scale);
    const y = origin.y + mapHeight - (event.clientY - box.top) / scale;
    return { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)) };
  }

  function insideMap(point: Point) {
    return (
      point.x >= origin.x &&
      point.x <= origin.x + mapWidth &&
      point.y >= origin.y &&
      point.y <= origin.y + mapHeight
    );
  }

  function handleClick(event: React.MouseEvent<SVGSVGElement>) {
    const point = svgPoint(event);
    if (!insideMap(point)) return;
    if (mode === "start") {
      onChange({ ...state, start: point });
      return;
    }
    if (mode === "end") {
      onChange({ ...state, end: point });
      return;
    }
    if (!draftPoint) {
      setDraftPoint(point);
      return;
    }
    const x = Math.min(draftPoint.x, point.x);
    const y = Math.min(draftPoint.y, point.y);
    const width = Math.abs(point.x - draftPoint.x);
    const height = Math.abs(point.y - draftPoint.y);
    setDraftPoint(null);
    if (width < project.resolution || height < project.resolution) return;
    if (mode === "closure") {
      const zone: ClosureZone = {
        id: crypto.randomUUID(),
        x,
        y,
        width,
        height,
        close_start: 0,
        close_end: 10,
      };
      onChange({ ...state, closure_zones: [...state.closure_zones, zone] });
      return;
    }
    const zone: ForbiddenZone = { id: crypto.randomUUID(), x, y, width, height };
    onChange({ ...state, forbidden_zones: [...state.forbidden_zones, zone] });
  }

  return (
    <div className="canvas-panel">
      <div className="tools">
        {([
          ["zone", "禁行区"],
          ["closure", "临时封路"],
          ["start", "起点"],
          ["end", "终点"],
        ] as Array<[Mode, string]>).map(([value, label]) => (
          <button
            key={value}
            className={mode === value ? "active" : ""}
            onClick={() => {
              setMode(value);
              setDraftPoint(null);
            }}
          >
            {label}
          </button>
        ))}
        <span>点击设置；禁行区点击两个对角点</span>
      </div>
      <svg
        width={mapWidth * scale}
        height={mapHeight * scale}
        className="map-svg"
        onClick={handleClick}
      >
        <g transform={`translate(0 ${mapHeight * scale}) scale(${scale} ${-scale})`}>
          <rect
            x={origin.x}
            y={origin.y}
            width={mapWidth}
            height={mapHeight}
            fill="#f8fafc"
            stroke="#0f172a"
          />
          {wallRects.map((rect, index) => (
            <rect
              key={index}
              x={rect.x}
              y={rect.y}
              width={project.resolution}
              height={project.resolution}
              fill="#334155"
            />
          ))}
          {state.forbidden_zones.map((zone) => (
            <rect
              key={zone.id}
              x={zone.x}
              y={zone.y}
              width={zone.width}
              height={zone.height}
              fill="rgba(239,68,68,0.35)"
              stroke="#dc2626"
              strokeWidth={2 / scale}
            />
          ))}
          {state.closure_zones.map((zone) => (
            <rect
              key={zone.id}
              x={zone.x}
              y={zone.y}
              width={zone.width}
              height={zone.height}
              fill="rgba(234,88,12,0.35)"
              stroke="#ea580c"
              strokeDasharray="0.12 0.06"
              strokeWidth={2 / scale}
            />
          ))}
          {result?.passable && (
            <path d={pathD} fill="none" stroke="#16a34a" strokeWidth={0.08} />
          )}
          {state.start && (
            <circle cx={state.start.x} cy={state.start.y} r={0.18} fill="#2563eb" />
          )}
          {state.end && (
            <circle cx={state.end.x} cy={state.end.y} r={0.18} fill="#9333ea" />
          )}
          {draftPoint && <circle cx={draftPoint.x} cy={draftPoint.y} r={0.12} fill="#ea580c" />}
          <line
            x1={origin.x}
            y1={origin.y}
            x2={origin.x + 0.8}
            y2={origin.y}
            stroke="#0f172a"
            strokeWidth={0.04}
          />
          <line
            x1={origin.x}
            y1={origin.y}
            x2={origin.x}
            y2={origin.y + 0.8}
            stroke="#0f172a"
            strokeWidth={0.04}
          />
        </g>
      </svg>
    </div>
  );
}
