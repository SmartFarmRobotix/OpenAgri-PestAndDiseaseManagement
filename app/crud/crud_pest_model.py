from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from crud.base import CRUDBase

from models import PestModel, Cultivation
from schemas import CreatePestModel, UpdatePestModel


class CrudPestModel(CRUDBase[PestModel, CreatePestModel, UpdatePestModel]):

    def create(self, db: Session, obj_in: CreatePestModel, **kwargs) -> Optional[PestModel]:

        pest_model_db = PestModel(
            name=obj_in.name,
            description=obj_in.description,
            geo_areas_of_application=obj_in.geo_areas_of_application,
            crops=obj_in.crops,
            eppo_code=obj_in.eppo_code,
        )

        # Attempt to create a pest model entity
        db.add(pest_model_db)
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            return None
        db.refresh(pest_model_db)

        # Assemble the cultivations from the list
        cultivations = [Cultivation(name=x, pest_model_id=pest_model_db.id) for x in (obj_in.cultivations or [])]

        # Attempt to create the cultivations and bind them to the previously created pest model entity
        db.add_all(cultivations)
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()

            db.delete(pest_model_db)
            try:
                db.commit()
            except SQLAlchemyError:
                return None

            return None

        for c in cultivations:
            db.refresh(c)

        db.refresh(pest_model_db)

        return pest_model_db


    def get_all(self, db: Session):
        return db.query(PestModel).all()

    def get_for_crop(self, db: Session, crop: str):
        """Return only pest models explicitly configured for ``crop``."""
        return [
            pest_model
            for pest_model in self.get_all(db=db)
            if isinstance(pest_model.crops, list) and crop in pest_model.crops
        ]


pest_model = CrudPestModel(PestModel)
