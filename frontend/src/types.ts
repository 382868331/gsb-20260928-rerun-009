export type Point = { x: number; y: number };

export type ForbiddenZone = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type MapState = {
  origin: Point;
  forbidden_zones: ForbiddenZone[];
  no_wait_zones: ForbiddenZone[];
  start: Point | null;
  end: Point | null;
  start_orientation: number;
  end_orientation: number;
  robot_width: number;
  robot_length: number;
  linear_speed: number;
  angular_speed: number;
  checkpoints: Checkpoint[];
};

export type Checkpoint = {
  id: string;
  name: string;
  x: number;
  y: number;
  orientation: number;
  earliest: number;
  latest: number;
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
  reason: string;
  algorithm: string;
  path: Array<Point & { orientation: number }>;
};

export type PathNode = Point & { orientation: number; row?: number; col?: number; layer?: number };

export type ScheduleEntry = {
  name: string;
  kind: string;
  arrival: number;
  depart?: number;
  wait?: number;
  path_index: number;
  wait_path_index?: number;
  earliest?: number;
  latest?: number;
};

export type TimedPlanResult = {
  plan_id?: number;
  status: string;
  status_label: string;
  reason: string;
  algorithm: string;
  path?: PathNode[];
  schedule?: ScheduleEntry[];
  total_time?: number;
};

export type PlanSummary = {
  id: number;
  name: string;
  kind: string;
  status: string;
  created_at: string;
};

export type RejudgeResult = {
  kept: TimedPlanResult;
  adjusted: TimedPlanResult;
};
