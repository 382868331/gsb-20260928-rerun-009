import { useMemo, useState } from "react";
import type { ForbiddenZone, MapState, Point, Project, ValidationResult } from "./types";

type Mode = "zone" | "timed" | "waiting" | "checkpoint" | "start" | "end";

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
    .map((point, index) => (index === 0 ? "M" : "L") + " " + point.x + " " + point.y)
    .join(" ");

  function svgPoint(event: React.MouseEvent<SVGSVGElement>): Point {
    const svg = event.currentTarget;
    const box = svg.getBoundingClientRect();
    const x = origin.x + (event.clientX - box.left) / scale;
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

  function addRect(nextMode: "zone" | "timed" | "waiting", point: Point) {
    const x = Math.min(draftPoint!.x, point.x);
    const y = Math.min(draftPoint!.y, point.y);
    const width = Math.abs(point.x - draftPoint!.x);
    const height = Math.abs(point.y - draftPoint!.y);
    setDraftPoint(null);
    if (width < project.resolution || height < project.resolution) return;
    const base = { id: crypto.randomUUID(), x, y, width, height };
    if (nextMode === "timed") {
      onChange({
        ...state,
        timed_zones: [...state.timed_zones, { ...base, close_start: 0, close_end: 30 }],
      });
    } else if (nextMode === "waiting") {
      onChange({ ...state, waiting_zones: [...state.waiting_zones, base] });
    } else {
      onChange({ ...state, forbidden_zones: [...state.forbidden_zones, base] });
    }
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
    if (mode === "checkpoint") {
      onChange({
        ...state,
        checkpoints: [
          ...state.checkpoints,
          {
            id: crypto.randomUUID(),
            order: state.checkpoints.length,
            x: point.x,
            y: point.y,
            orientation: 0,
            window_start: 0,
            window_end: 60,
            tolerance: null,
            orientation_tolerance: null,
          },
        ],
      });
      return;
    }
    if (!draftPoint) {
      setDraftPoint(point);
      return;
    }
    addRect(mode, point);
  }

  const tools: Array<[Mode, string]> = [
    ["zone", "永久禁行区"],
    ["timed", "限时封闭区"],
    ["waiting", "安全等待区"],
    ["checkpoint", "检查点"],
    ["start", "起点"],
    ["end", "终点"],
  ];

  return (
    <div className="canvas-panel">
      <div className="tools">
        {tools.map(([value, label]) => (
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
        <span>点击设置；矩形工具点击两个对角点</span>
      </div>
      <svg
        width={mapWidth * scale}
        height={mapHeight * scale}
        className="map-svg"
        onClick={handleClick}
      >
        <g transform={"translate(0 " + mapHeight * scale + ") scale(" + scale + " " + -scale + ")"}>
          <rect x={origin.x} y={origin.y} width={mapWidth} height={mapHeight} fill="#f8fafc" stroke="#0f172a" />
          {wallRects.map((rect, index) => (
            <rect key={index} x={rect.x} y={rect.y} width={project.resolution} height={project.resolution} fill="#334155" />
          ))}
          {state.forbidden_zones.map((zone) => (
            <rect key={zone.id} x={zone.x} y={zone.y} width={zone.width} height={zone.height}
              fill="rgba(239,68,68,0.35)" stroke="#dc2626" strokeWidth={2 / scale} />
          ))}
          {state.timed_zones.map((zone) => (
            <rect key={zone.id} x={zone.x} y={zone.y} width={zone.width} height={zone.height}
              fill="rgba(245,158,11,0.30)" stroke="#d97706"
              strokeDasharray="0.18 0.12" strokeWidth={2 / scale} />
          ))}
          {state.waiting_zones.map((zone) => (
            <rect key={zone.id} x={zone.x} y={zone.y} width={zone.width} height={zone.height}
              fill="rgba(34,197,94,0.22)" stroke="#16a34a"
              strokeDasharray="0.1 0.08" strokeWidth={2 / scale} />
          ))}
          {result?.passable && (
            <path d={pathD} fill="none" stroke="#16a34a" strokeWidth={0.08} />
          )}
          {state.checkpoints.map((cp) => (
            <circle key={cp.id} cx={cp.x} cy={cp.y} r={0.16} fill="#0ea5e9" stroke="#0c4a6e" strokeWidth={0.02} />
          ))}
          {state.start && <circle cx={state.start.x} cy={state.start.y} r={0.18} fill="#2563eb" />}
          {state.end && <circle cx={state.end.x} cy={state.end.y} r={0.18} fill="#9333ea" />}
          {draftPoint && <circle cx={draftPoint.x} cy={draftPoint.y} r={0.12} fill="#ea580c" />}
        </g>
      </svg>
    </div>
  );
}
