"""Update schemas treat an explicit null for a column that can't be empty as "unchanged"."""

from typing import Any, ClassVar

from pydantic import BaseModel, model_validator


class IgnoresNullForRequiredColumns(BaseModel):
    """Base for update (PATCH/PUT) schemas that are applied to one ORM row.

    Their fields are mostly ``X | None = None`` so a request can leave them out,
    and the API's contract is that null means "unchanged": most routes apply a
    field only when it is not None, and the OpenAPI schema allows null. Routes
    that apply what was sent with ``model_dump(exclude_unset=True)`` instead
    wrote the null into a NOT NULL column and failed with a 500. This drops such
    a null before validation, so the field counts as not sent on every route; a
    null for a column that may be empty still clears it.

    ``__orm_table__`` names the table the schema writes to. It is looked up when
    a request is validated, so schemas don't import the models.
    """

    __orm_table__: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def _ignore_null_for_required_columns(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        columns = _table(cls.__orm_table__).c
        by_key = {(info.alias or name): name for name, info in cls.model_fields.items()} | {
            name: name for name in cls.model_fields
        }
        return {
            key: value
            for key, value in data.items()
            if not (value is None and key in by_key and by_key[key] in columns and not columns[by_key[key]].nullable)
        }


def _table(name: str):
    from backend.app.core.database import load_all_models

    return load_all_models().tables[name]
