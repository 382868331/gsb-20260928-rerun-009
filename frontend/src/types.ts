export type Point = { x: number; y: number };

export type ForbiddenZone = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ClosureZone = ForbiddenZone & {
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
  tolerance?: number | null;
  orientation_tolerance?: number | null;
};

export type MapState = {
  origin: Point;
  forbidden_zones: ForbiddenZone[];
  closure_zones: ClosureZone[];
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

export type ValidationResult = {
  passable: boolean;
  status: string;
  reason: string;
  algorithm: string;
  path: Array<Point & { orientation: number }>;
  schedule?: ScheduleEvent[];
  segments?: ReplaySegment[];
  search_completed?: boolean;
  geometry_kept?: boolean;
};

export type ScheduleEvent = {
  event: string;
  time: number;
  wait_until?: number;
  x: number;
  y: number;
  orientation: number;
  checkpoints_passed?: number[];
};

export type ReplaySegment = {
  seq: number;
  action: string;
  t_start: number;
  t_end: number;
  from: Point & { orientation: number };
  to: Point & { orientation: number };
  checkpoints_passed?: number[];
};
