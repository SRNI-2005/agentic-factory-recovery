"""Instance lookup + lineage loaders."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from coe.db.models.provenance import Instance
from coe.services.schemas import InstanceOut


def get_row(session: Session, name: str) -> Instance | None:
    """Single name query; instances.name is UNIQUE so at most one match."""
    return session.query(Instance).filter(Instance.name == name).one_or_none()


def _lineage_rows(session: Session, name: str | None) -> list:
    sql = ("SELECT i.name, i.source_name, p.name AS parent "
           "FROM instances i "
           "LEFT JOIN scenario_sources ss "
           "  ON ss.scenario_id = i.id AND ss.contribution_type = 'fork' "
           "LEFT JOIN instances p ON p.id = ss.source_instance_id ")
    params: dict = {}
    if name is not None:
        sql += "WHERE i.name = :name "
        params["name"] = name
    sql += "ORDER BY i.name ASC"
    return session.execute(text(sql), params).all()


def list_instances(session: Session) -> list[InstanceOut]:
    """Fork lineage per the provenance row written by services.fork:
    ScenarioSource(scenario_id=fork, source_instance_id=parent,
    contribution_type='fork')."""
    rows = _lineage_rows(session, None)
    return [InstanceOut(name=r.name, source_name=r.source_name,
                        parent=r.parent) for r in rows]


def get(session: Session, name: str) -> InstanceOut | None:
    """Single instance with fork-lineage parent; None when unknown."""
    r = _lineage_rows(session, name)
    if not r:
        return None
    return InstanceOut(name=r[0].name, source_name=r[0].source_name,
                       parent=r[0].parent)
