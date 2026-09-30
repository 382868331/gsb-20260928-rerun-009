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


class MapStateModel(BaseModel):
    origin: PointModel
    forbidden_zones: list[ZoneModel] = Field(default_factory=list)
    start: PointModel | None = None
    end: PointModel | None = None
    start_orientation: float = Field(default=0.0)
    end_orientation: float = Field(default=0.0)
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
