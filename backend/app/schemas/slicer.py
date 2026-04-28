"""Pydantic schemas for slice requests."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PresetRef(BaseModel):
    """A source-aware reference to a printer / process / filament preset.

    The SliceModal pulls dropdown options from three tiers (cloud / local /
    standard). At submit time the client sends one of these per slot so the
    backend knows where to fetch the preset content from at slice time.
    """

    source: Literal["cloud", "local", "standard"]
    id: str = Field(..., description=("Cloud setting_id, local DB row id (stringified), or standard preset name."))


class SliceRequest(BaseModel):
    """Body for `POST /library/files/{file_id}/slice`.

    Two preset shapes are accepted per slot for backwards-compatibility:

    - **Legacy** — bare integer ``*_preset_id`` fields point into the
      ``local_presets`` table. Existing clients (and stale browser tabs after
      a Bambuddy upgrade) keep working unchanged.
    - **Source-aware** — ``*_preset`` carries an explicit
      ``{source, id}``. Required for cloud / standard tiers; also accepted
      (and equivalent) for local presets when the client is on the new modal.

    Exactly one of each pair must be set; the validator normalises legacy
    integer ids into a ``PresetRef(source='local', id=str(id))`` so the
    downstream resolver only deals with one shape.
    """

    # Legacy fields — kept optional so older clients continue to work.
    printer_preset_id: int | None = Field(
        default=None,
        description="DEPRECATED: prefer printer_preset. LocalPreset id with preset_type='printer'.",
    )
    process_preset_id: int | None = Field(
        default=None,
        description="DEPRECATED: prefer process_preset. LocalPreset id with preset_type='process'.",
    )
    filament_preset_id: int | None = Field(
        default=None,
        description="DEPRECATED: prefer filament_preset. LocalPreset id with preset_type='filament'.",
    )

    # Source-aware fields — set by the new SliceModal.
    printer_preset: PresetRef | None = None
    process_preset: PresetRef | None = None
    filament_preset: PresetRef | None = None

    # Multi-color: one PresetRef per AMS slot the source plate uses. Order is
    # significant — the slicer matches index-by-index against the plate's
    # filament slots. Always preferred over the legacy singular field; the
    # validator promotes a singular field into ``[singular]`` when the list
    # is empty so older clients keep working.
    filament_presets: list[PresetRef] = Field(default_factory=list)

    plate: int | None = Field(
        default=None,
        ge=1,
        description="Plate number to slice (1-indexed). Defaults to plate 1 on the sidecar.",
    )
    export_3mf: bool = Field(
        default=False,
        description="If true, request a 3MF response with embedded G-code instead of raw G-code.",
    )

    @model_validator(mode="after")
    def normalise_preset_refs(self) -> "SliceRequest":
        """Each slot must end up with a `PresetRef` set. Legacy integer ids
        become `(source='local', id=str(int))` so the route handler only
        deals with the canonical shape. For filament: a non-empty
        ``filament_presets`` list satisfies the requirement on its own; an
        empty list falls back to the singular fields, which then promote
        into a one-element list."""
        for slot, ref_attr, legacy_attr in (
            ("printer", "printer_preset", "printer_preset_id"),
            ("process", "process_preset", "process_preset_id"),
        ):
            ref = getattr(self, ref_attr)
            legacy_id = getattr(self, legacy_attr)
            if ref is None and legacy_id is None:
                raise ValueError(
                    f"{slot} preset is required: provide '{ref_attr}' (preferred) or legacy '{legacy_attr}'"
                )
            if ref is None:
                setattr(self, ref_attr, PresetRef(source="local", id=str(legacy_id)))

        # Filament accepts THREE shapes, in priority order:
        #   1. filament_presets    — multi-color array (new clients)
        #   2. filament_preset     — source-aware singular (single-color new clients)
        #   3. filament_preset_id  — legacy bare integer (old clients)
        # The first non-empty shape wins; missing all three raises.
        if not self.filament_presets:
            if self.filament_preset is not None:
                self.filament_presets = [self.filament_preset]
            elif self.filament_preset_id is not None:
                fallback = PresetRef(source="local", id=str(self.filament_preset_id))
                self.filament_preset = fallback
                self.filament_presets = [fallback]
            else:
                raise ValueError(
                    "filament preset is required: provide 'filament_presets' (preferred), "
                    "'filament_preset', or legacy 'filament_preset_id'"
                )
        elif self.filament_preset is None:
            # Multi-color caller: backfill the singular from the first slot
            # so callers that still read the legacy field see a stable value.
            self.filament_preset = self.filament_presets[0]
        return self


class SliceResponse(BaseModel):
    """Response from `POST /library/files/{file_id}/slice`. The result lands
    in the user's library as a new ``LibraryFile`` (in the same folder as
    the source)."""

    library_file_id: int
    name: str
    print_time_seconds: int
    filament_used_g: float
    filament_used_mm: float
    used_embedded_settings: bool = False


class SliceArchiveResponse(BaseModel):
    """Response from `POST /archives/{archive_id}/slice`. The result lands
    in the user's archives as a new ``PrintArchive`` row, inheriting
    printer / project metadata from the source archive."""

    archive_id: int
    name: str
    print_time_seconds: int
    filament_used_g: float
    filament_used_mm: float
    used_embedded_settings: bool = False
