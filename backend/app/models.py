from pydantic import BaseModel, Field


class PointModel(BaseModel):
    x: float
    y: float


class PoseModel(PointModel):
    # Radians; null checkpoints only require passing the position.
    orientation: float | None = None


class ZoneModel(BaseModel):
    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class MapStateModel(BaseModel):
    origin: PointModel
    forbidden_zones: list[ZoneModel] = Field(default_factory=list)
    start: PoseModel | None = None
    end: PoseModel | None = None
    checkpoints: list[PoseModel] = Field(default_factory=list)
    robot_width: float = Field(default=0.5, gt=0)
    robot_length: float = Field(default=0.8, gt=0)


class ProjectUpdateModel(BaseModel):
    state: MapStateModel
    note: str = ""


class VersionCreateModel(BaseModel):
    name: str
    state: MapStateModel


class PassageRequestModel(BaseModel):
    state: MapStateModel
