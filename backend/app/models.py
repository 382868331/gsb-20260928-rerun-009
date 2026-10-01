from pydantic import BaseModel, Field


class PointModel(BaseModel):
    x: float
    y: float


class ZoneModel(BaseModel):
    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class CheckpointModel(BaseModel):
    id: str
    name: str = ""
    x: float
    y: float
    # Degrees, matching the UI. Converted to radians inside the planners.
    orientation: float = 0.0
    earliest: float = Field(default=0.0, ge=0)
    latest: float = Field(default=60.0, ge=0)


class MapStateModel(BaseModel):
    origin: PointModel
    forbidden_zones: list[ZoneModel] = Field(default_factory=list)
    no_wait_zones: list[ZoneModel] = Field(default_factory=list)
    start: PointModel | None = None
    end: PointModel | None = None
    start_orientation: float = 0.0
    end_orientation: float = 0.0
    robot_width: float = Field(default=0.5, gt=0)
    robot_length: float = Field(default=0.8, gt=0)
    linear_speed: float = Field(default=0.5, gt=0)
    angular_speed: float = Field(default=90.0, gt=0)
    checkpoints: list[CheckpointModel] = Field(default_factory=list)


class ProjectUpdateModel(BaseModel):
    state: MapStateModel
    note: str = ""


class VersionCreateModel(BaseModel):
    name: str
    state: MapStateModel


class PassageRequestModel(BaseModel):
    state: MapStateModel


class TimedPlanRequestModel(BaseModel):
    state: MapStateModel
    name: str = ""


class RejudgeRequestModel(BaseModel):
    checkpoints: list[CheckpointModel]
