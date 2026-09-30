import { useMemo, useState } from "react";
import type { ForbiddenZone, MapState, Point, Project, ValidationResult } from "./types";

type Mode = "zone" | "start" | "end";

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
  const footprintStep = Math.max(1, Math.floor(pathPoints.length / 24));
  const footprintNodes = pathPoints.filter(
    (_, index) => index % footprintStep === 0 || index === pathPoints.length - 1
  );

  function footprintPolygon(point: { x: number; y: number; orientation: number }) {
    const halfLength = state.robot_length / 2;
    const halfWidth = state.robot_width / 2;
    const corners = [
      [-halfLength, -halfWidth],
      [halfLength, -halfWidth],
      [halfLength, halfWidth],
      [-halfLength, halfWidth],
    ];
    const cosine = Math.cos(point.orientation);
    const sine = Math.sin(point.orientation);
    return corners.map(([lx, ly]) => {
      const x = point.x + lx * cosine - ly * sine;
      const y = point.y + lx * sine + ly * cosine;
      return x + "," + y;
    }).join(" ");
  }

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
    const zone: ForbiddenZone = {
      id: crypto.randomUUID(),
      x,
      y,
      width: Math.abs(point.x - draftPoint.x),
      height: Math.abs(point.y - draftPoint.y),
    };
    setDraftPoint(null);
    if (zone.width >= project.resolution && zone.height >= project.resolution) {
      onChange({ ...state, forbidden_zones: [...state.forbidden_zones, zone] });
    }
  }

  return (
    <div className="canvas-panel">
      <div className="tools">
        {([
          ["zone", "禁行区"],
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
          {result?.passable && (
            <path d={pathD} fill="none" stroke="#16a34a" strokeWidth={0.08} />
          )}
          {result?.passable && footprintNodes.map((point, index) => (
            <polygon
              key={index}
              points={footprintPolygon(point)}
              fill="rgba(22,163,74,0.12)"
              stroke="#15803d"
              strokeWidth={0.015}
            />
          ))}
          {state.start && (
            <>
              <polygon
                points={footprintPolygon({
                  x: state.start.x,
                  y: state.start.y,
                  orientation: (state.start_orientation * Math.PI) / 180,
                })}
                fill="rgba(37,99,235,0.18)"
                stroke="#2563eb"
                strokeWidth={0.02}
              />
              <circle cx={state.start.x} cy={state.start.y} r={0.08} fill="#2563eb" />
            </>
          )}
          {state.end && (
            <>
              <polygon
                points={footprintPolygon({
                  x: state.end.x,
                  y: state.end.y,
                  orientation: (state.end_orientation * Math.PI) / 180,
                })}
                fill="rgba(147,51,234,0.18)"
                stroke="#9333ea"
                strokeWidth={0.02}
              />
              <circle cx={state.end.x} cy={state.end.y} r={0.08} fill="#9333ea" />
            </>
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
