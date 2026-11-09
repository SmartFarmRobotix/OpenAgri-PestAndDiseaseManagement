import datetime
import operator
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
import pandas as pd

import crud
import utils
import uuid

from models import PestModel, Parcel
from .wdutils import openmeteo_friendly_variables

prob_values = {
    "low": 1,
    "moderate": 2,
    "high": 3
}

SAFE_OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "=": operator.eq,
    "!=": operator.ne,
}

def _get_operator(symbol: str):
    if symbol not in SAFE_OPERATORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported operator symbol: {symbol}"
        )
    return SAFE_OPERATORS[symbol]

def _safe_compare(left: Optional[float], symbol: str, right: float) -> bool:
    if left is None:
        return False
    op = _get_operator(symbol)
    return op(left, right)


def _agri_pest(pm: PestModel) -> dict:
    """Build the JSON-LD pest identity from the evaluated model's catalogue data."""
    code = getattr(pm, "eppo_code", None)
    pest = {
        "@id": f"urn:openagri:pest:{code or pm.name}",
        "@type": "AgriPest",
        "name": pm.name,
    }
    if code:
        pest["eppoConcept"] = f"https://gd.eppo.int/taxon/{code}"
    return pest

def _row_satisfies_conditions(row: pd.Series, conditions: list) -> bool:
    for cond in conditions:
        if cond.value is None:
            return False
        left = row.get(cond.unit.name)
        if not _safe_compare(left, cond.operator.symbol, float(cond.value)):
            return False
    return True

def calculate_risk_index_probability(db: Session, parcel: Parcel, pest_models: List[PestModel],
                                     from_date: datetime.date, to_date:datetime.date,
                                     parameter: Optional[str] = None):
    # SQL query for the data
    data_db = crud.data.get_data_query_by_parcel_id_and_date_interval(db=db, parcel_id=parcel.id,
                                                                      date_from=from_date, date_to=to_date)

    df = pd.read_sql(sql=data_db.statement, con=db.bind, parse_dates={"date": "%Y-%m-%d"})

    # Calculate the risks associated with each pest_model
    for pm in pest_models:
        risks_for_current_pm = ["Low"] * df.shape[0]

        for rule in pm.rules:
            conditions = rule.conditions

            df_with_risk = df.assign(
                risk=df.apply(lambda row: _row_satisfies_conditions(row, conditions), axis=1)
            )

            # With this, only one rule should only ever fire for one singular date/time weather data point.
            # If multiple rules turn up as valid (both are true), then the last one from the db is going to have its
            # prob. value written as a final response for this pest model
            risks_for_current_pm = [rule.probability_value if x else y for x, y in zip(df_with_risk["risk"], risks_for_current_pm)]

        df["{}".format(pm.name)] = risks_for_current_pm

    context = utils.context

    graph = []

    for pm in pest_models:

        calculated_risks = []
        for date, time, risk in zip(df["date"], df["time"], df["{}".format(pm.name)]):

            if parameter and risk != parameter:
                continue

            calculated_risks.append(
                {
                    "@id": "urn:openagri:pestInfectationRisk:obs2:{}".format(uuid.uuid4()),
                    "@type": ["Observation", "PestInfestationRisk"],
                    "phenomenonTime": "{}".format(str(date).split(" ")[0] + "T" + str(time)),
                    "hasSimpleResult": "{}".format(risk)
                }
            )

        graph_element = {
            "@id": "urn:openagri:pestInfectationRisk:{}".format(uuid.uuid4()),
            "@type": ["ObservationCollection"],
            "description": "{} pest infectation risk forecast in x ".format(pm.name),
            "observedProperty": {
                "@id": "urn:openagri:pestInfectationRisk:op:{}".format(uuid.uuid4()),
                "@type": ["ObservableProperty", "PestInfection"],
                "name": f"{pm.name} pest infection",
                "hasAgriPest": _agri_pest(pm),
            },
            "madeBySensor": {
                "@id": "urn:openagri:pestInfectationRisk:model:{}".format(uuid.uuid4()),
                "@type": ["Sensor", "AIPestDetectionModel"],
                "name": "AI pest detaction model xyz"
            },
            "hasFeatureOfInterest": {
                "@id": "urn:openagri:pestInfectationRisk:foi:{}".format(uuid.uuid4()),
                "@type": ["FeatureOfInterest", "Point"],
                "long": "{}".format(parcel.longitude),
                "lat": "{}".format(parcel.latitude)
            },
            "basedOnWeatherDataset": {
                "@id": "urn:openagri:weatherDataset:{}".format(parcel.id),
                "@type": "WeatherDataset",
                "name": "parcel_name_tba"
            },
            "resultTime": "{}".format(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
            "hasMember": calculated_risks
        }

        graph.append(graph_element)

    doc = {
        "@context": context,
        "@graph": graph
    }

    return doc

def calculate_risk_index_probability_wd(
        parcel: dict,
        pest_models: List[PestModel],
        weather_data: dict,
        lat: float,
        lon: float,
        parameter: Optional[str] = None,
        growth_stage_escalate: bool = False,
):
    """
    ``growth_stage_escalate``:
    when True, an hour that the weather rules alone evaluate to "moderate"
    is escalated to "high". This is how a concrete growth stage enters the
    rule evaluation itself, rather than being copied into provenance after
    the fact — the caller sets it when the session's growth stage is a
    documented high-consequence stage for the pest being evaluated (e.g.
    BRVCBR head-formation, the literature and the OCSM catalogue notes). It never
    manufactures risk from nothing: a "low" hour (no weather signal at all)
    stays "low" regardless of growth stage, since no sourced quantitative
    growth-stage-only threshold exists --
    only the compounding of an already-elevated weather signal is
    encoded, which is a qualitative, defensible judgement rather than an
    invented number. Default False leaves every existing caller/test
    (generic pests with no growth-stage concept) byte-for-byte unaffected.
    """
    graph = []

    for pm in pest_models:
        calculated_risks = []

        for hour in weather_data["data"]:

            current_rule_risk_index = "low"
            for rule in pm.rules:

                # Filter, if parameter set to "high", skip low and medium ones
                if parameter and prob_values[rule.probability_value] < prob_values[parameter]:
                    continue

                # Skip these since their calculation wouldn't meaningfully add to the current model limits
                if rule.probability_value and prob_values[rule.probability_value] <= prob_values[current_rule_risk_index]:
                    continue

                current_rule_applies = True
                for cond in rule.conditions:
                    if cond.value is None:
                        current_rule_applies = False
                        break
                    value_key = openmeteo_friendly_variables.get(cond.unit.name)
                    if not value_key:
                        current_rule_applies = False
                        break
                    left = hour["values"].get(value_key)
                    if not _safe_compare(left, cond.operator.symbol, float(cond.value)):
                        current_rule_applies = False
                        break

                if current_rule_applies:
                    current_rule_risk_index = rule.probability_value

            if growth_stage_escalate and current_rule_risk_index == "moderate":
                current_rule_risk_index = "high"

            if not parameter:
                calculated_risks.append(
                    {
                        "@id": "urn:openagri:pestInfectationRisk:obs2:{}".format(uuid.uuid4()),
                        "@type": ["Observation", "PestInfestationRisk"],
                        "phenomenonTime": "{}".format(hour["timestamp"]),
                        "hasSimpleResult": "{}".format(current_rule_risk_index)
                    }
                )
                continue

            if parameter and prob_values[current_rule_risk_index] >= prob_values[parameter]:
                calculated_risks.append(
                    {
                        "@id": "urn:openagri:pestInfectationRisk:obs2:{}".format(uuid.uuid4()),
                        "@type": ["Observation", "PestInfestationRisk"],
                        "phenomenonTime": "{}".format(hour["timestamp"]),
                        "hasSimpleResult": "{}".format(current_rule_risk_index)
                    }
                )
                continue


        graph_element = {
            "@id": "urn:openagri:pestInfectationRisk:{}".format(uuid.uuid4()),
            "@type": ["ObservationCollection"],
            "description": "{} pest infectation risk forecast in x ".format(pm.name),
            "observedProperty": {
                "@id": "urn:openagri:pestInfectationRisk:op:{}".format(uuid.uuid4()),
                "@type": ["ObservableProperty", "PestInfection"],
                "name": f"{pm.name} pest infection",
                "hasAgriPest": _agri_pest(pm),
            },
            "madeBySensor": {
                "@id": "urn:openagri:pestInfectationRisk:model:{}".format(uuid.uuid4()),
                "@type": ["Sensor", "AIPestDetectionModel"],
                "name": "AI pest detaction model xyz"
            },
            "hasFeatureOfInterest": {
                "@id": "urn:openagri:pestInfectationRisk:foi:{}".format(uuid.uuid4()),
                "@type": ["FeatureOfInterest", "Point"],
                "long": "{}".format(lon),
                "lat": "{}".format(lat)
            },
            "basedOnWeatherDataset": {
                "@id": "urn:openagri:weatherDataset:{}".format(parcel["@id"]),
                "@type": "WeatherDataset",
                "name": "parcel_name_tba"
            },
            "resultTime": "{}".format(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
            "hasMember": calculated_risks
        }

        graph.append(graph_element)

    doc = {
        "@context": utils.context,
        "@graph": graph
    }

    return doc
