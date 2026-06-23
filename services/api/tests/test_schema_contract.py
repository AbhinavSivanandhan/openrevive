import app.models  # noqa: F401

from app.db.base import Base


def test_core_schema_tables_are_registered() -> None:
    assert {"workspaces", "collections"} <= set(Base.metadata.tables)


def test_collections_belong_to_workspaces() -> None:
    collections = Base.metadata.tables["collections"]

    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in collections.c.workspace_id.foreign_keys
    }

    assert foreign_keys == {"workspaces.id"}
