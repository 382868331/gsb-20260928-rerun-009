export type Point = { x: number; y: number };

export type ForbiddenZone = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type TimedZone = ForbiddenZone & {
  close_start: number;
  close_end: number;
};

export type Checkpoint = {
  id: string;
  order: number;
  x: number;
  y: number;
  orientation: number;
  window_start: number;
  window_end: number;
  tolerance: number | null;
  orientation_tolerance: number | null;
};

export type MapState = {
  origin: Point;
  forbidden_zones: ForbiddenZone[];
  timed_zones: TimedZone[];
  waiting_zones: ForbiddenZone[];
  checkpoints: Checkpoint[];
  start: Point | null;
  end: Point | null;
  robot_width: number;
  robot_length: number;
  start_orientation: number;
  end_orientation: number | null;
  linear_speed: number;
  angular_speed: number;
  start_time: number;
};

export type Project = {
  id: number;
  name: string;
  resolution: number;
  width: number;
  height: number;
  state: MapState;
  grid: boolean[][];
  original_url: string;
  updated_at: string;
};

export type Version = {
  id: number;
  name: string;
  created_at: string;
};

export type Segment = {
  type: "start" | "arrive" | "wait_depart_arrive" | "turn" | "wait" | "checkpoint";
  start?: number;
  end?: number;
  time?: number;
  x: number;
  y: number;
  orientation?: number;
  order?: number;
  window_start?: number;
  window_end?: number;
};

export type Replay = {
  ok: boolean;
  segment_count: number;
  checkpoint_times: Record<string, number>;
  failures: Array<{ index: number; type: string }>;
  missing_checkpoints: number[];
};

export type ValidationResult = {
  passable: boolean;
  status: string;
  reason: string;
  algorithm: string;
  path: Array<Point & { orientation: number }>;
  schedule?: Array<Record<string, unknown>>;
  segments?: Segment[];
  replay?: Replay;
  stage?: string;
  retain_basis?: Record<string, unknown> | null;
  retain_geometry?: { status: string; reason: string; arrival_time?: number };
  search_completed?: boolean;
};
