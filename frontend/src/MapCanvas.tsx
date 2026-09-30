import { useMemo, useState } from "react";
import type { ForbiddenZone, MapState, Point, Project, ValidationResult } from "./types";

type Mode = "zone" | "start" | "end" | "checkpoint";

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
      onChange({ ...state, start: { ...point, orientation: state.start?.orientation ?? 0 } });
      return;
    }
    if (mode === "end") {
      onChange({ ...state, end: { ...point, orientation: state.end?.orientation ?? 0 } });
      return;
    }
    if (mode === "checkpoint") {
      onChange({
        ...state,
        checkpoints: [
          ...state.checkpoints,
          { ...point, orientation: 0 },
        ],
      });
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
          ["checkpoint", "检查点"],
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
        <span>点击设置；禁行区点击两个对角点；检查点在侧栏填写指定朝向</span>
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
          {result?.passable &&
            result.path.map((pose, index) => (
              <g
                key={index}
                transform={`translate(${pose.x} ${pose.y}) rotate(${(pose.orientation * 180) / Math.PI})`}
              >
                <rect x={-0.15} y={-0.3} width={0.3} height={0.6} fill="none" stroke="#15803d" strokeWidth={0.02} />
              </g>
            ))}
          {state.start && (
            <g transform={`translate(${state.start.x} ${state.start.y}) rotate(${((state.start.orientation ?? 0) * 180) / Math.PI})`}>
              <circle r={0.18} fill="#2563eb" />
              <line x1={0} y1={0} x2={0} y2={0.32} stroke="white" strokeWidth={0.05} />
            </g>
          )}
          {state.end && (
            <g transform={`translate(${state.end.x} ${state.end.y}) rotate(${((state.end.orientation ?? 0) * 180) / Math.PI})`}>
              <circle r={0.18} fill="#9333ea" />
              <line x1={0} y1={0} x2={0} y2={0.32} stroke="white" strokeWidth={0.05} />
            </g>
          )}
          {state.checkpoints.map((checkpoint, index) => (
            <g
              key={index}
              transform={`translate(${checkpoint.x} ${checkpoint.y}) rotate(${((checkpoint.orientation ?? 0) * 180) / Math.PI})`}
            >
              <polygon points="0,0.28 0.14,0 -0.14,0" fill="#ea580c" />
              <text x={0.22} y={-0.12} fontSize={0.3} fill="#9a3412">{index + 1}</text>
            </g>
          ))}
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
