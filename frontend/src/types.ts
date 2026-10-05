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
  state_defaults?: string[];
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
  passable?: boolean;
  status: string;
  reason?: string;
  algorithm?: string;
  path?: Array<Point & { orientation: number }>;
  schedule?: Array<Record<string, unknown>>;
  segments?: Segment[];
  replay?: Replay;
  stage?: string;
  retain_basis?: Record<string, unknown> | null;
  retain_geometry?: { status: string; reason: string; arrival_time?: number };
  search_completed?: boolean;
  map_version?: string;
  map_fingerprint?: string;
  robot_params?: Record<string, number | null>;
  state_snapshot?: MapState;
  plans?: Array<Record<string, unknown>>;
};

export type SavedPlan = {
  id: number;
  created_at: string;
  result: ValidationResult;
  state: MapState;
  result_complete: boolean;
  missing_fields: string[];
  map_changed: boolean;
  map_snapshot_missing: boolean;
  map_version: string | null;
  current_map_version?: string;
  map_fingerprint?: string | null;
  current_map_fingerprint?: string | null;
};

export type PlanReplay = {
  consistent: boolean;
  saved_status?: string;
  fresh_status: string;
  saved_passable?: boolean;
  fresh_passable?: boolean;
  saved_reason?: string;
  fresh_reason?: string;
  saved_search_completed?: boolean;
  fresh_search_completed?: boolean;
  saved_segments: Segment[];
  fresh_segments: Segment[];
  saved_path?: Array<Point & { orientation: number }>;
  fresh_path?: Array<Point & { orientation: number }>;
  saved_schedule?: Array<Record<string, unknown>>;
  fresh_schedule?: Array<Record<string, unknown>>;
  replay: Replay;
  algorithm?: string;
  saved_algorithm?: string;
  result_complete: boolean;
  missing_fields: string[];
  map_changed: boolean;
  map_snapshot_missing: boolean;
  saved_map_version?: string | null;
  current_map_version?: string;
  robot_params?: Record<string, number>;
};

export type JointTask = {
  id: string;
  start: Point;
  end: Point;
  start_orientation?: number;
  end_orientation?: number | null;
  linear_speed?: number | null;
  angular_speed?: number | null;
  robot_width?: number | null;
  robot_length?: number | null;
  earliest_departure: number;
  latest_departure: number;
  committed: boolean;
  committed_departure?: number | null;
};

export type JointCandidate = {
  candidate_id?: number;
  objective: "min_total_wait" | "min_makespan";
  departures: Record<string, number>;
  total_wait: number;
  makespan: number;
  conflict_free: boolean;
  provisional?: boolean;
  tasks: Record<
    string,
    {
      departure: number;
      arrival_time: number;
      wait: number;
      status: string;
      segments: Segment[];
    }
  >;
};

export type JointPlanResult = {
  status: "feasible" | "infeasible" | "search_incomplete";
  reason: string;
  algorithm?: string;
  search_completed: boolean;
  evaluated_combinations: number;
  total_combinations: number;
  time_quantum: number;
  single_feasible: Record<string, boolean>;
  candidates: JointCandidate[];
  rejection?: {
    task_failure_combinations: number;
    conflict_combinations: number;
    sample_task_failure?: Record<string, unknown> | null;
    sample_conflict?: Record<string, unknown> | null;
  };
  map_fingerprint?: string;
};

export type SavedJointCandidate = {
  id: number;
  objective: string;
  created_at: string;
  adopted_at: string | null;
  result: JointCandidate;
  task_ids: string[];
  commitments: Record<string, number>;
  valid: boolean;
  invalid_reason: string | null;
};
