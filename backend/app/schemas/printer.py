from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PrinterBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    serial_number: str = Field(..., min_length=1, max_length=50)

    @field_validator("serial_number")
    @classmethod
    def _normalize_serial_number(cls, v: str) -> str:
        """Uppercase and trim the serial number.

        Bambu serial numbers are uppercase alphanumeric, and the MQTT report
        topic ``device/<serial>/report`` is case-sensitive. A serial entered
        in the wrong case (or with stray whitespace) connects and subscribes
        without error but never receives a message — the printer publishes to
        the correctly-cased topic, so every status field stays unknown (#1465).
        Normalising on input makes the subscribed topic always match.
        """
        normalized = v.strip().upper()
        if not normalized:
            raise ValueError("serial_number must not be blank")
        return normalized

    ip_address: str = Field(
        ...,
        max_length=253,
        pattern=r"^(\d{1,3}(\.\d{1,3}){3}|[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*)$",
    )
    model: str | None = None
    location: str | None = None  # Group/location name
    auto_archive: bool = True
    external_camera_url: str | None = None
    external_camera_type: str | None = None  # "mjpeg", "rtsp", "snapshot", "usb"
    external_camera_enabled: bool = False
    external_camera_snapshot_url: str | None = None  # Optional single-frame override; #1177
    camera_rotation: int = 0  # 0, 90, 180, 270 degrees


class PrinterCreate(PrinterBase):
    # access_code lives on the input shapes only — never on the default
    # PrinterResponse. Direct exposure on PRINTERS_READ would let a Viewer
    # connect to the printer's MQTT and bypass Bambuddy's RBAC.
    access_code: str = Field(..., min_length=1, max_length=20)


class PlateDetectionROI(BaseModel):
    """Region of interest for plate detection (percentages 0.0-1.0)."""

    x: float = Field(..., ge=0.0, le=1.0)  # X start %
    y: float = Field(..., ge=0.0, le=1.0)  # Y start %
    w: float = Field(..., ge=0.0, le=1.0)  # Width %
    h: float = Field(..., ge=0.0, le=1.0)  # Height %


class PrinterUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = Field(
        default=None,
        max_length=253,
        pattern=r"^(\d{1,3}(\.\d{1,3}){3}|[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*)$",
    )
    access_code: str | None = None
    model: str | None = None
    location: str | None = None
    is_active: bool | None = None
    auto_archive: bool | None = None
    print_hours_offset: float | None = None
    external_camera_url: str | None = None
    external_camera_type: str | None = None
    external_camera_enabled: bool | None = None
    external_camera_snapshot_url: str | None = None  # #1177
    camera_rotation: int | None = None  # 0, 90, 180, 270 degrees
    plate_detection_enabled: bool | None = None
    plate_detection_roi: PlateDetectionROI | None = None


class PrinterResponse(PrinterBase):
    id: int
    is_active: bool
    nozzle_count: int = 1  # 1 or 2, auto-detected from MQTT
    print_hours_offset: float = 0.0
    external_camera_url: str | None = None
    external_camera_type: str | None = None
    external_camera_enabled: bool = False
    external_camera_snapshot_url: str | None = None  # #1177
    camera_rotation: int = 0  # 0, 90, 180, 270 degrees
    plate_detection_enabled: bool = False
    plate_detection_roi: PlateDetectionROI | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_roi(cls, printer) -> "PrinterResponse":
        """Create response from ORM model, converting ROI fields to nested object."""
        data = {
            "id": printer.id,
            "name": printer.name,
            "serial_number": printer.serial_number,
            "ip_address": printer.ip_address,
            "model": printer.model,
            "location": printer.location,
            "auto_archive": printer.auto_archive,
            "external_camera_url": printer.external_camera_url,
            "external_camera_type": printer.external_camera_type,
            "external_camera_enabled": printer.external_camera_enabled,
            "external_camera_snapshot_url": printer.external_camera_snapshot_url,
            "camera_rotation": printer.camera_rotation,
            "is_active": printer.is_active,
            "nozzle_count": printer.nozzle_count,
            "print_hours_offset": printer.print_hours_offset,
            "plate_detection_enabled": printer.plate_detection_enabled,
            "created_at": printer.created_at,
            "updated_at": printer.updated_at,
        }
        # Build ROI object if any ROI field is set
        if any(
            [
                printer.plate_detection_roi_x is not None,
                printer.plate_detection_roi_y is not None,
                printer.plate_detection_roi_w is not None,
                printer.plate_detection_roi_h is not None,
            ]
        ):
            data["plate_detection_roi"] = PlateDetectionROI(
                x=printer.plate_detection_roi_x or 0.15,
                y=printer.plate_detection_roi_y or 0.35,
                w=printer.plate_detection_roi_w or 0.70,
                h=printer.plate_detection_roi_h or 0.55,
            )
        return cls(**data)


class PrinterResponseWithSecret(PrinterResponse):
    """PrinterResponse + access_code. Returned ONLY to callers with
    PRINTERS_UPDATE (Admin / Operator JWTs, or single-trust auth-disabled mode).

    Viewers and API keys never receive this shape — they get the bare
    PrinterResponse without access_code, since holding the access_code lets
    the caller talk to the printer's MQTT directly and bypass Bambuddy's RBAC.
    """

    access_code: str


class HMSErrorResponse(BaseModel):
    code: str
    attr: int = 0  # Attribute value for constructing wiki URL
    module: int
    severity: int  # 1=fatal, 2=serious, 3=common, 4=info
    actions: list[str] = []  # List of user-facing action keys (e.g. "CHECK_FILAMENT")
    job_id: str | None = None  # Optional job ID for actions that require it (e.g. "CHECK_ASSISTANT")
    # Canonical hex identifier the firmware uses to match HMS-related commands.
    # 16 chars for `hms[]`-array faults (full 64-bit attr+code), 8 chars for
    # `print_error` faults. The frontend echoes this back as
    # HmsActionBody.print_error so we send the firmware-recognised key, not the
    # truncated short_code that historically caused silent command rejection
    # (#1830, H2D wrong-plate verification).
    full_code: str = ""


class AMSTray(BaseModel):
    id: int
    tray_color: str | None = None
    tray_type: str | None = None
    tray_sub_brands: str | None = None  # Full name like "PLA Basic", "PETG HF"
    tray_id_name: str | None = None  # Bambu filament ID like "A00-Y2" (can decode to color)
    tray_info_idx: str | None = None  # Filament preset ID like "GFA00"
    remain: int = 0
    k: float | None = None  # Pressure advance value (from tray or K-profile lookup)
    cali_idx: int | None = None  # Calibration index for K-profile lookup
    tag_uid: str | None = None  # RFID tag UID (any tag)
    tray_uuid: str | None = None  # Bambu Lab spool UUID (32-char hex)
    nozzle_temp_min: int | None = None  # Min nozzle temperature
    nozzle_temp_max: int | None = None  # Max nozzle temperature
    drying_temp: int | None = None  # RFID-recommended drying temp
    drying_time: int | None = None  # RFID-recommended drying time (hours)
    state: int | None = None  # AMS tray state: 9=empty, 10=spool present not loaded, 11=loaded
    # Firmware's authoritative "spool physically present" bit (from tray_exist_bits).
    # True for a non-RFID spool the firmware can't identify — the UI shows "?" rather
    # than "Empty" (#2527). None when the bitmask was unavailable (→ state-based fallback).
    exists: bool | None = None


class AMSUnit(BaseModel):
    id: int
    humidity: int | None = None
    temp: float | None = None
    is_ams_ht: bool = False  # True for AMS-HT (single spool), False for regular AMS (4 spools)
    tray: list[AMSTray] = []
    serial_number: str = ""  # AMS unit serial number (sn from MQTT)
    sw_ver: str = ""  # AMS firmware version (from get_version info.module)
    dry_time: int = 0  # Minutes remaining (0 = not drying, >0 = drying active)
    dry_status: int = 0  # 0=Off, 1=Checking, 2=Drying, 3=Cooling, 4=Stopping, 5=Error
    dry_sub_status: int = 0  # 0=Off, 1=Heating, 2=Dehumidify
    dry_sf_reason: list[int] = []  # Cannot-dry reasons from firmware (see CannotDryReason)
    dry_target_temp: int | None = None  # Active-cycle target °C (Bambu doesn't echo this)
    dry_filament: str | None = None  # Active-cycle filament name we sent
    module_type: str = ""  # "ams", "n3f", "n3s"


class NozzleInfoResponse(BaseModel):
    nozzle_type: str = ""  # "stainless_steel" or "hardened_steel"
    nozzle_diameter: str = ""  # e.g., "0.4"


class NozzleRackSlot(BaseModel):
    """H2C nozzle rack slot (6-position tool-changer dock)."""

    id: int = 0
    nozzle_type: str = ""
    nozzle_diameter: str = ""
    wear: int | None = None
    stat: int | None = None  # Nozzle status (e.g. mounted/docked)
    max_temp: int = 0  # Max temperature rating °C (0 = not set)
    serial_number: str = ""  # Nozzle serial number
    filament_color: str = ""  # RGBA hex ("00000000" = no filament)
    filament_id: str = ""  # Bambu filament ID
    filament_type: str = ""  # Material type (e.g. "PLA", "PETG")


class AmsLabelBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    ams_serial: str = Field(default="", max_length=50)


class HmsActionBody(BaseModel):
    # Canonical hex identifier (HMSErrorResponse.full_code): 8 chars for
    # `print_error`-sourced faults, 16 chars for `hms[]`-array faults whose
    # full 64-bit code is the firmware's matching key. Length-bounded to
    # those two valid shapes to keep stray input from reaching the dispatcher.
    print_error: str = Field(..., min_length=8, max_length=16, pattern=r"^[0-9A-Fa-f]{8}([0-9A-Fa-f]{8})?$")
    # One of the HMSAction enum values. Length-capped to keep stray input from
    # reaching the dispatcher's `match` statement.
    action: str = Field(..., min_length=1, max_length=64)
    # The `subtask_id` snapshot from the HMSError that surfaced this dialog.
    # Bambu echoes it back in HMS-aware commands. Optional for idle errors.
    job_id: str | None = Field(default=None, max_length=64)


class FilaSwitchResponse(BaseModel):
    """Filament Track Switch (FTS) state — accessory that mediates AMS-to-extruder routing.

    When installed, the AMS info field reports bits 8-11 = 0xE (uninitialized)
    because slots are dynamically routed via the FTS rather than tied to a
    specific extruder. Frontend uses `installed` to suppress the per-extruder
    slot filter in the print modal. See #1162.
    """

    installed: bool = False
    # in[track] = currently loaded slot for that track (-1 = empty)
    in_slots: list[int] = []
    # out[track] = extruder this track terminates at (0 = right, 1 = left)
    out_extruders: list[int] = []
    stat: int = 0
    info: int = 0


class PrintOptionsResponse(BaseModel):
    """AI detection and print options from xcam data."""

    # Core AI detectors
    spaghetti_detector: bool = False
    print_halt: bool = False
    halt_print_sensitivity: str = "medium"  # Spaghetti sensitivity
    first_layer_inspector: bool = False
    printing_monitor: bool = False
    buildplate_marker_detector: bool = False
    allow_skip_parts: bool = False
    # Additional AI detectors (decoded from cfg bitmask)
    nozzle_clumping_detector: bool = True
    nozzle_clumping_sensitivity: str = "medium"
    pileup_detector: bool = True
    pileup_sensitivity: str = "medium"
    airprint_detector: bool = True
    airprint_sensitivity: str = "medium"
    auto_recovery_step_loss: bool = True
    filament_tangle_detect: bool = False


class PrinterStatus(BaseModel):
    id: int
    name: str
    connected: bool
    state: str | None = None
    current_print: str | None = None
    subtask_name: str | None = None
    gcode_file: str | None = None
    progress: float | None = None
    remaining_time: int | None = None
    layer_num: int | None = None
    total_layers: int | None = None
    temperatures: dict | None = None
    cover_url: str | None = None
    hms_errors: list[HMSErrorResponse] = []
    ams: list[AMSUnit] = []
    ams_exists: bool = False
    vt_tray: list[AMSTray] = []  # Virtual tray / external spool(s)
    sdcard: bool = False  # SD card inserted
    store_to_sdcard: bool = False  # Store sent files on SD card
    timelapse: bool = False  # Timelapse recording active
    ipcam: bool = False  # Live view enabled
    wifi_signal: int | None = None  # WiFi signal strength in dBm
    wired_network: bool = False  # Ethernet connection detected
    door_open: bool = False  # Enclosure door open (X1/P1S/P2S/H2*)
    nozzles: list[NozzleInfoResponse] = []  # Nozzle hardware info (index 0=left/primary, 1=right)
    nozzle_rack: list[NozzleRackSlot] = []  # H2C 6-nozzle tool-changer rack
    print_options: PrintOptionsResponse | None = None  # AI detection and print options
    # Calibration stage tracking
    stg_cur: int = -1  # Current stage number (-1 = not calibrating)
    stg_cur_name: str | None = None  # Human-readable current stage name
    stg: list[int] = []  # List of stage numbers in calibration sequence
    # Air conditioning mode (0=cooling, 1=heating)
    airduct_mode: int = 0
    # Print speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
    speed_level: int = 2
    # Chamber light on/off
    chamber_light: bool = False
    # Active extruder for dual nozzle (0=right, 1=left)
    active_extruder: int = 0
    # AMS mapping for dual nozzle: which AMS is connected to which nozzle
    ams_mapping: list[int] = []
    # Per-AMS extruder map: {ams_id: extruder_id} where 0=right, 1=left
    ams_extruder_map: dict[str, int] = {}
    # Filament Track Switch (FTS) accessory — when installed, AMS reports
    # bits 8-11 = 0xE (uninitialized) and routing is dynamic via the FTS. See #1162.
    fila_switch: FilaSwitchResponse | None = None
    # Currently loaded tray (global ID): 254 = external spool, 255 = no filament
    tray_now: int = 255
    # AMS status for filament change tracking
    # Main status: 0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration
    ams_status_main: int = 0
    # Sub status: specific step within filament change (when main=1)
    # Known values: 4=retraction, 6=load verification, 7=purge
    ams_status_sub: int = 0
    # mc_print_sub_stage - filament change step indicator used by OrcaSlicer/BambuStudio
    mc_print_sub_stage: int = 0
    # Timestamp of last AMS data update (for RFID refresh detection)
    last_ams_update: float = 0.0
    # Number of printable objects in current print (for skip objects feature)
    printable_objects_count: int = 0
    # Fan speeds (0-100 percentage, None if not available for this model)
    cooling_fan_speed: int | None = None  # Part cooling fan
    big_fan1_speed: int | None = None  # Auxiliary fan
    big_fan2_speed: int | None = None  # Chamber/exhaust fan
    heatbreak_fan_speed: int | None = None  # Hotend heatbreak fan
    # Firmware version (from info.module[name="ota"].sw_ver)
    firmware_version: str | None = None
    # Developer LAN mode: True = enabled, False = disabled (MQTT encryption), None = unknown
    developer_mode: bool | None = None
    # AMS Filament Backup ("auto-switch" to a second spool when one runs out).
    # True = ON, False = OFF, None = unknown / unsupported (A1 family — protocol field
    # not yet identified). UI treats None as "status unavailable", not as a hard disable.
    ams_filament_backup: bool | None = None
    # Queue: printer is awaiting the user to acknowledge the build plate is cleared
    # after a finished/failed print. Persisted across restarts (#961).
    awaiting_plate_clear: bool = False
    # AMS drying support
    supports_drying: bool = False
    # AMS "Print While Drying" — drying mid-print. Verified per Bambu wiki release notes;
    # see _DRY_WHILE_PRINTING_MIN_FIRMWARE in printer_manager.py for the matrix.
    supports_drying_while_printing: bool = False
    # Active chamber heater (responds to M141). True only for H2C/H2D/H2DPro/H2S/X2D.
    supports_chamber_heater: bool = False
    # Linked archive for the active print (resolved via subtask_id). Frontend uses
    # this to fetch plate metadata and show the plate name when the source 3MF is
    # multi-plate (#881 follow-up).
    current_archive_id: int | None = None
    # 1-indexed plate number parsed from gcode_file (e.g. /Metadata/plate_2.gcode).
    # Set for every active print regardless of plate count; the frontend decides
    # whether to render it based on current_archive_id's is_multi_plate flag.
    current_plate_id: int | None = None


class DiagnosticCheck(BaseModel):
    """One connection-diagnostic check result.

    ``id`` is a stable key (port_mqtt, port_ftps, port_rtsps, network_mode,
    subnet, mqtt_auth, developer_mode); the frontend renders the localized
    title and fix text from id + status. ``params`` carries interpolation
    values (e.g. network mode, IP addresses) for that text.
    """

    id: str
    status: str  # "pass" | "fail" | "warn" | "skip"
    params: dict = Field(default_factory=dict)


class PrinterDiagnosticResult(BaseModel):
    """Result of a printer connection diagnostic run."""

    printer_id: int | None = None
    ip_address: str
    overall: str  # "ok" | "warnings" | "problems"
    checks: list[DiagnosticCheck]


class DiagnosticRequest(BaseModel):
    """Pre-save (Add Printer) connection diagnostic request.

    serial_number + access_code are optional: when both are present the
    diagnostic also probes MQTT credentials, otherwise only the
    network-level checks run.
    """

    ip_address: str
    serial_number: str | None = None
    access_code: str | None = None
