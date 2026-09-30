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
  start: Point | null;
  end: Point | null;
  start_orientation: number;
  end_orientation: number;
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
  id?: number;
  passable: boolean;
  reason: string;
  algorithm: string;
  path: Array<Point & { orientation: number }>;
};

export type CheckRecord = {
  id: number;
  passable: boolean;
  reason: string;
  algorithm: string;
  state: MapState;
  result: ValidationResult;
  created_at: string;
};
