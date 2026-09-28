export type Point = { x: number; y: number }
export type Rect = { id: string; x: number; y: number; width: number; height: number }
export type Origin = { x: number; y: number; yaw: number }
export type Revision = { id: number; label: string; created_at: string; origin: Origin; forbidden_zones: Rect[] }
export type CellPose = Point & { yaw: number }
export type VerificationResult = {
  feasible: boolean
  reason: string
  robot_width: number
  robot_length: number
  start: Point
  goal: Point
  orientations: number
  path: CellPose[]
  checked_at: string
}
export type Project = {
  id: string
  name: string
  original_filename: string
  image_path: string
  width: number
  height: number
  resolution: number
  origin: Origin
  forbidden_zones: Rect[]
  revisions: Revision[]
  last_verification: VerificationResult | null
}
