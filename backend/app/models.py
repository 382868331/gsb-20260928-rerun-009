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
    order: int = Field(ge=0)
    x: float
    y: float
    orientation: float = 0.0
    window_start: float = Field(ge=0)
    window_end: float = Field(ge=0)
    tolerance: float | None = None
    orientation_tolerance: float | None = None


class MapStateModel(BaseModel):
    origin: PointModel
    forbidden_zones: list[ZoneModel] = Field(default_factory=list)
    waiting_zones: list[ZoneModel] = Field(default_factory=list)
    checkpoints: list[CheckpointModel] = Field(default_factory=list)
    start: PointModel | None = None
    end: PointModel | None = None
    robot_width: float = Field(default=0.5, gt=0)
    robot_length: float = Field(default=0.8, gt=0)
    start_orientation: float = 0.0
    end_orientation: float | None = None
    linear_speed: float = Field(default=0.5, gt=0)
    angular_speed: float = Field(default=0.5, gt=0)
    start_time: float = Field(default=0.0, ge=0)


class ProjectUpdateModel(BaseModel):
    state: MapStateModel
    note: str = ""


class VersionCreateModel(BaseModel):
    name: str
    state: MapStateModel


class PassageRequestModel(BaseModel):
    state: MapStateModel
    keep_geometry: bool = False
    geometric_path: list[dict] | None = None
