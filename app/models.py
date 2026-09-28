from __future__ import annotations

from pydantic import BaseModel, Field


class Point(BaseModel):
    x: float
    y: float


class Rect(BaseModel):
    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Origin(BaseModel):
    x: float = 0
    y: float = 0
    yaw: float = 0


class Robot(BaseModel):
    width: float = Field(gt=0)
    length: float = Field(gt=0)


class Revision(BaseModel):
    id: int
    label: str
    created_at: str
    origin: Origin
    forbidden_zones: list[Rect]


class Project(BaseModel):
    id: str
    name: str
    original_filename: str
    image_path: str
    normalized_image_path: str
    width: int
    height: int
    resolution: float
    origin: Origin
    forbidden_zones: list[Rect] = []
    revisions: list[Revision] = []
    last_verification: "VerificationResult | None" = None


class ProjectUpdate(BaseModel):
    origin: Origin | None = None
    forbidden_zones: list[Rect] | None = None
    revision_label: str | None = None


class RollbackRequest(BaseModel):
    revision_id: int
    label: str = "回滚版本"


class VerificationRequest(BaseModel):
    robot: Robot
    start: Point
    goal: Point
    orientations: int = Field(default=24, ge=4, le=72)
    save: bool = True


class CellPose(BaseModel):
    x: float
    y: float
    yaw: float


class VerificationResult(BaseModel):
    feasible: bool
    reason: str
    robot_width: float
    robot_length: float
    start: Point
    goal: Point
    orientations: int
    path: list[CellPose] = []
    checked_at: str


class VerifyResponse(BaseModel):
    project: Project
    result: VerificationResult
