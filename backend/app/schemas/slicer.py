"""Pydantic schemas for slice requests."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# `#RRGGBB` or `#RRGGBBAA`. Bambu Studio writes the 6-digit form into
# `filament_colour` but accepts and round-trips the 8-digit one, and the AMS
# reports colours with an alpha byte, so both have to pass.
_HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


class PresetRef(BaseModel):
    """A source-aware reference to a printer / process / filament preset.

    The SliceModal pulls dropdown options from four tiers (orca_cloud /
    cloud / local / standard). At submit time the client sends one of these
    per slot so the backend knows where to fetch the preset content from at
    slice time. ``cloud`` is Bambu Cloud (kept as the bare name for backward
    compatibility with existing requests); ``orca_cloud`` is Orca Cloud.
    """

    source: Literal["orca_cloud", "cloud", "local", "standard"]
    id: str = Field(
        ...,
        description=(
            "Orca Cloud profile id, Bambu Cloud setting_id, local DB row id (stringified), or standard preset name."
        ),
    )


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

    # Per-slot filament colour, plate-slot-ordered like ``filament_presets``.
    # Neither Bambu Studio nor OrcaSlicer store a colour on a *filament preset*
    # — it is a per-project property their GUIs set from the plate — so the CLI
    # falls back to its compiled-in default (#00AE42, Bambu green) for every
    # slice unless something supplies one. That default is what #2977 saw: a
    # green plate thumbnail, `filament_colour = #00AE42` in the output, and a
    # "Color mismatch" against the AMS slot the print was mapped to.
    #
    # `default_filament_colour` is NOT a substitute. Measured against a
    # 02.08.02.61 sidecar: sending it alone leaves `filament_colour` at
    # #00AE42, because the CLI never reads it — it is consumed by the GUI when
    # initialising a project. The colour has to be written to `filament_colour`
    # itself, which is what this field ends up doing.
    filament_colours: list[str] = Field(
        default_factory=list,
        description=(
            "Per-slot filament colour as ``#RRGGBB`` / ``#RRGGBBAA``, in the same "
            "plate-slot order as ``filament_presets``. Written onto each resolved "
            "filament profile as ``filament_colour`` so the sliced file records the "
            "colour actually being printed instead of the slicer's built-in default "
            "(#2977). A shorter list than ``filament_presets`` leaves the remaining "
            "slots to the fallback chain; an empty string in any position does the "
            "same for that one slot. An omitted list (older clients) falls back to "
            "the preset's own ``default_filament_colour``, then to the colour the "
            "source file's plate was designed with."
        ),
    )

    plate: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Plate number to slice. ``None`` defaults to plate 1 on the sidecar "
            "(matches the pre-multi-plate behaviour). ``0`` is the sidecar's "
            "'all plates' sentinel — produces a single multi-plate 3MF whose "
            "``Metadata/plate_N.gcode`` entries cover every plate in the "
            "source. ``>= 1`` slices that one plate."
        ),
    )
    export_3mf: bool = Field(
        default=False,
        description="If true, request a 3MF response with embedded G-code instead of raw G-code.",
    )
    design_overrides: list[str] | None = Field(
        default=None,
        description=(
            "3MF only. Process setting keys from the source file's "
            "``different_settings_to_system`` to carry onto the picked process "
            "preset (#2622) — the designer's own wall count, infill, first-layer "
            "height and so on, which ``--load-settings`` would otherwise discard. "
            "Only keys the source actually lists as changed are applied; anything "
            "else is ignored. An empty list is not the same answer as ``None``: "
            "it says the caller was shown the file's settings and chose none of "
            "them, which also holds back the support carry-over (#1881) for the "
            "support keys the file offered, while ``None`` — a caller that "
            "predates the per-key choice — leaves that carry-over unconditional."
        ),
    )
    process_overrides: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The user's own process-setting edits from the slice modal's settings "
            "panel, as a sparse ``{option_key: value}`` map (layer height, wall "
            "count, supports, speeds — OrcaSlicer's process parameter set). Written "
            "into the process JSON *after* the source's support settings and the "
            "designer's carried tweaks, so an explicit choice here wins over both. "
            "Values are normalised to the string forms a process preset stores; "
            "keys that aren't valid config keys are dropped rather than failing "
            "the slice. ``None``/empty leaves the picked preset untouched."
        ),
    )
    use_embedded_settings: bool = Field(
        default=False,
        description=(
            "3MF only. Slice using the file's embedded "
            "``Metadata/project_settings.config`` (the designer's own tweaks — wall "
            "count, infill, etc.) instead of the picked printer/process/filament "
            "triplet. This is the 'slice as designed' path: no ``--load-settings`` "
            "override, so a MakerWorld author's settings survive. Ignored for STL / "
            "plain-model 3MF (no embedded profile to honour). The preset refs are "
            "still required by the validator but go unused on this path. Only makes "
            "sense when the picked printer matches the design's target model — the "
            "UI gates the toggle on that; there is no cross-printer re-targeting here "
            "(that is exactly what the profile path is for)."
        ),
    )
    bed_type: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Override the process preset's curr_bed_type for this slice. Canonical "
            "BambuStudio / OrcaSlicer values: 'Cool Plate', 'Engineering Plate', "
            "'High Temp Plate', 'Textured PEI Plate', 'Smooth PEI Plate', "
            "'Cool Plate (SuperTack)', 'Supertack Plate'. None ⇒ inherit from the "
            "process preset unchanged (#1337)."
        ),
    )
    auto_orient: bool = Field(
        default=False,
        description=(
            "Let the slicer pick each object's orientation before slicing "
            "(BambuStudio / OrcaSlicer ``--orient 1``, the GUI's 'Auto orient'). "
            "Off by default: it rotates geometry, so a model the designer laid "
            "flat on purpose would silently change. Applies on the embedded-"
            "settings path too — it is a CLI action, not a profile value (#2548)."
        ),
    )
    auto_arrange: bool = Field(
        default=False,
        description=(
            "Let the slicer lay the objects out on the plate before slicing "
            "(``--arrange 1``, the GUI's 'Auto arrange'). Off by default: it "
            "repositions objects, discarding a deliberate layout. Forced on "
            "regardless for cross-nozzle-class re-slices, where the source's "
            "coordinates land in the target's dead zone (#1493). Applies on the "
            "embedded-settings path too (#2548)."
        ),
    )

    @model_validator(mode="after")
    def normalise_preset_refs(self) -> "SliceRequest":
        """Each slot must end up with a `PresetRef` set. Legacy integer ids
        become `(source='local', id=str(int))` so the route handler only
        deals with the canonical shape. For filament: a non-empty
        ``filament_presets`` list satisfies the requirement on its own; an
        empty list falls back to the singular fields, which then promote
        into a one-element list.
        """
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

        # Colours are pasted straight into a profile the slicer parses, so a
        # malformed one is rejected here rather than passed through. Empty
        # strings survive: they are how a caller says "no colour for this
        # slot" without having to shorten the list and shift every slot after
        # it. Normalised to upper-case so a slice never differs from another
        # only by the case of a hex digit.
        normalised: list[str] = []
        for i, colour in enumerate(self.filament_colours):
            value = (colour or "").strip()
            if not value:
                normalised.append("")
                continue
            if not _HEX_COLOUR.fullmatch(value):
                raise ValueError(f"filament_colours[{i}] must be '#RRGGBB' or '#RRGGBBAA', got {colour!r}")
            normalised.append("#" + value[1:].upper())
        self.filament_colours = normalised
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
    # Set when the source lives in an external folder that could not receive
    # the result (read-only, unreachable, not writable), so the file went to
    # managed storage instead. Names which of those it was. ``None`` on every
    # normal slice. Reported rather than silently absorbed: filing the output
    # somewhere the user isn't looking, with no signal, is what made #2810
    # impossible to reproduce from the UI.
    external_write_fallback: str | None = None


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
