export type Point = { x: number; y: number };

export type Pose = Point & { orientation: number | null };

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
  start: Pose | null;
  end: Pose | null;
  checkpoints: Pose[];
  robot_width: number;
  robot_length: number;
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
  status:
    | "passable"
    | "orientation-unreachable"
    | "passage-blocked"
    | "route-not-found"
    | "start-invalid";
  failed_stage: string | null;
  reason: string;
  algorithm: string;
  path: Array<Point & { orientation: number }>;
  angular_resolution: number;
};
