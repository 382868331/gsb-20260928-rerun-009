from pydantic import BaseModel, Field, field_validator


class PointModel(BaseModel):
    x: float
    y: float


class ZoneModel(BaseModel):
    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class TimedZoneModel(BaseModel):
    """区域在 [close_start, close_end) 半开区间内禁止进入。

    close_start 时刻必须已经完全撤离；close_end 时刻起允许进入。
    """

    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    close_start: float = Field(ge=0)
    close_end: float = Field(ge=0)

    @field_validator("close_end")
    @classmethod
    def _end_after_start(cls, value, info):
        start = info.data.get("close_start", 0.0)
        if value < start:
            raise ValueError("timed zone close end precedes start")
        return value


class CheckpointModel(BaseModel):
    id: str
    order: int = Field(ge=0)
    x: float
    y: float
    orientation: float = 0.0
    window_start: float = Field(default=0.0, ge=0)
    window_end: float = 0.0
    tolerance: float | None = None
    orientation_tolerance: float | None = None

    @field_validator("window_end")
    @classmethod
    def _end_after_start(cls, value, info):
        start = info.data.get("window_start", 0.0)
        if value < start:
            raise ValueError("checkpoint window end precedes start")
        return value


class MapStateModel(BaseModel):
    origin: PointModel
    forbidden_zones: list[ZoneModel] = Field(default_factory=list)
    timed_zones: list[TimedZoneModel] = Field(default_factory=list)
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
    search_budget: int = Field(default=1_500_000, gt=0)
