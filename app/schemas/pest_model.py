from typing import Optional, List

from pydantic import BaseModel, ConfigDict, UUID4


class CreatePestModel(BaseModel):
    name: str
    description: Optional[str] = None
    geo_areas_of_application: Optional[str] = None
    cultivations: Optional[List[str]] = None
    crops: Optional[List[str]] = None
    eppo_code: Optional[str] = None

class UpdatePestModel(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    geo_areas_of_application: Optional[str] = None
    crops: Optional[List[str]] = None
    eppo_code: Optional[str] = None

class PestModelDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID4
    name: str
    description: Optional[str]
    geo_areas_of_application: Optional[str]
    crops: Optional[List[str]] = None
    eppo_code: Optional[str] = None

class PestModels(BaseModel):
    pests: List[PestModelDB]
