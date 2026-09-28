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
  reason: string;
  algorithm: string;
  path: Array<Point & { orientation: number }>;
};
