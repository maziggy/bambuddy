import json

from pydantic import BaseModel, Field, field_validator


class AppSettings(BaseModel):
    """Application settings schema."""

    auto_archive: bool = Field(default=True, description="Automatically archive prints when completed")
    save_thumbnails: bool = Field(default=True, description="Extract and save preview images from 3MF files")
    capture_finish_photo: bool = Field(
        default=True, description="Capture photo from printer camera when print completes"
    )
    default_filament_cost: float = Field(default=25.0, description="Default filament cost per kg")
    currency: str = Field(default="USD", description="Currency for cost tracking")
    energy_cost_per_kwh: float = Field(default=0.15, description="Electricity cost per kWh for energy tracking")
    energy_tracking_mode: str = Field(
        default="total",
        description="Energy display mode on stats: 'print' shows sum of per-print energy, 'total' shows lifetime plug consumption",
    )

    # Spoolman integration
    spoolman_enabled: bool = Field(default=False, description="Enable Spoolman integration for filament tracking")
    spoolman_url: str = Field(default="", description="Spoolman server URL (e.g., http://localhost:7912)")
    spoolman_sync_mode: str = Field(
        default="auto", description="Sync mode: 'auto' syncs immediately, 'manual' requires button press"
    )
    spoolman_disable_weight_sync: bool = Field(
        default=False,
        description="Disable remaining_weight sync. When enabled, only location is updated for existing spools.",
    )
    spoolman_report_partial_usage: bool = Field(
        default=True,
        description="Report Partial Usage for Failed Prints. When a print fails or is cancelled, report the estimated filament used up to that point based on layer progress.",
    )
    disable_filament_warnings: bool = Field(
        default=False,
        description="Disable insufficient filament warnings when printing or queueing prints",
    )
    prefer_lowest_filament: bool = Field(
        default=False,
        description="When multiple AMS spools match, prefer the one with lowest remaining filament",
    )

    # Updates
    check_updates: bool = Field(default=True, description="Automatically check for updates on startup")
    check_printer_firmware: bool = Field(default=True, description="Check for printer firmware updates from Bambu Lab")
    include_beta_updates: bool = Field(default=False, description="Include beta/prerelease versions in update checks")

    # Language
    language: str = Field(default="en", description="UI language (en, de, fr, ja, it, pt-BR)")
    notification_language: str = Field(default="en", description="Language for push notifications (en, de)")

    # Bed cooled notification threshold
    bed_cooled_threshold: float = Field(
        default=35.0, description="Bed temperature threshold for cooled notification (°C)"
    )

    # AMS threshold settings for humidity and temperature coloring
    ams_humidity_good: int = Field(default=40, description="Humidity threshold for good (green): <= this value")
    ams_humidity_fair: int = Field(
        default=60, description="Humidity threshold for fair (orange): <= this value, > is red"
    )
    ams_temp_good: float = Field(default=28.0, description="Temperature threshold for good (blue): <= this value")
    ams_temp_fair: float = Field(
        default=35.0, description="Temperature threshold for fair (orange): <= this value, > is red"
    )
    ams_history_retention_days: int = Field(default=30, description="Number of days to keep AMS sensor history data")

    # Queue auto-drying settings
    queue_drying_enabled: bool = Field(
        default=False, description="Automatically dry AMS filament between queued prints"
    )
    queue_drying_block: bool = Field(
        default=False,
        description="Block queue until drying completes (when disabled, prints take priority over drying)",
    )
    ambient_drying_enabled: bool = Field(
        default=False,
        description="Automatically dry AMS filament on idle printers when humidity exceeds threshold, regardless of queue",
    )
    drying_presets: str = Field(
        default="",
        description="JSON blob of drying presets per filament type (empty = use built-in defaults)",
    )

    # Auto-print G-code injection (#422)
    gcode_snippets: str = Field(
        default="",
        description="JSON: per-model G-code injection snippets {model: {start_gcode, end_gcode}}",
    )

    # Scheduled local backup (#884)
    local_backup_enabled: bool = Field(default=False, description="Enable scheduled local backups")
    local_backup_schedule: str = Field(default="daily", description="Backup frequency: hourly, daily, weekly")
    local_backup_time: str = Field(default="03:00", description="Time of day for daily/weekly backups (HH:MM, 24h)")
    local_backup_retention: int = Field(default=5, description="Number of backup files to keep (1-100)")
    local_backup_path: str = Field(default="", description="Backup output directory (empty = DATA_DIR/backups)")

    # Print modal settings
    per_printer_mapping_expanded: bool = Field(
        default=False, description="Expand custom filament mapping by default in print modal"
    )

    # Date/time display format
    date_format: str = Field(default="system", description="Date format: system, us, eu, iso")
    time_format: str = Field(default="system", description="Time format: system, 12h, 24h")

    # Default printer for operations
    default_printer_id: int | None = Field(default=None, description="Default printer ID for uploads, reprints, etc.")

    # Virtual Printer
    virtual_printer_enabled: bool = Field(default=False, description="Enable virtual printer for slicer uploads")
    virtual_printer_access_code: str = Field(default="", description="Access code for virtual printer authentication")
    virtual_printer_mode: str = Field(
        default="immediate",
        description="Mode: 'immediate' (archive now), 'review' (pending review), or 'print_queue' (add to print queue)",
    )

    # Dark mode theme settings
    dark_style: str = Field(default="classic", description="Dark mode style: classic, glow, vibrant")
    dark_background: str = Field(
        default="neutral", description="Dark mode background: neutral, warm, cool, oled, slate, forest"
    )
    dark_accent: str = Field(default="green", description="Dark mode accent: green, teal, blue, orange, purple, red")

    # Light mode theme settings
    light_style: str = Field(default="classic", description="Light mode style: classic, glow, vibrant")
    light_background: str = Field(default="neutral", description="Light mode background: neutral, warm, cool")
    light_accent: str = Field(default="green", description="Light mode accent: green, teal, blue, orange, purple, red")

    # FTP retry settings for unreliable WiFi connections
    ftp_retry_enabled: bool = Field(default=True, description="Enable automatic retry for FTP operations")
    ftp_retry_count: int = Field(default=3, description="Number of retry attempts for FTP operations (1-10)")
    ftp_retry_delay: int = Field(default=2, description="Seconds to wait between FTP retry attempts (1-30)")
    ftp_timeout: int = Field(default=30, description="FTP connection timeout in seconds (10-300)")

    # MQTT Relay settings for publishing events to external broker
    mqtt_enabled: bool = Field(default=False, description="Enable MQTT event publishing to external broker")
    mqtt_broker: str = Field(default="", description="MQTT broker hostname or IP address")
    mqtt_port: int = Field(default=1883, description="MQTT broker port (default 1883, TLS typically 8883)")
    mqtt_username: str = Field(default="", description="MQTT username for authentication (optional)")
    mqtt_password: str = Field(default="", description="MQTT password for authentication (optional)")
    mqtt_topic_prefix: str = Field(default="bambuddy", description="Topic prefix for all published messages")
    mqtt_use_tls: bool = Field(default=False, description="Use TLS/SSL encryption for MQTT connection")

    # External URL for notifications
    external_url: str = Field(
        default="", description="External URL where Bambuddy is accessible (for notification images)"
    )

    # Home Assistant integration for smart plug control
    ha_enabled: bool = Field(default=False, description="Enable Home Assistant integration for smart plug control")
    ha_url: str = Field(default="", description="Home Assistant URL (e.g., http://192.168.1.100:8123)")
    ha_token: str = Field(default="", description="Home Assistant Long-Lived Access Token")
    ha_url_from_env: bool = Field(default=False, description="Whether HA URL is set via HA_URL environment variable")
    ha_token_from_env: bool = Field(
        default=False, description="Whether HA token is set via HA_TOKEN environment variable"
    )
    ha_env_managed: bool = Field(
        default=False, description="Whether HA integration is fully managed by environment variables"
    )

    # File Manager / Library settings
    library_archive_mode: str = Field(
        default="ask",
        description="When printing from File Manager, create archive entry: 'always', 'never', or 'ask'",
    )
    library_disk_warning_gb: float = Field(
        default=5.0,
        description="Show warning when free disk space falls below this threshold (GB)",
    )

    # Camera view settings
    camera_view_mode: str = Field(
        default="window",
        description="Camera view mode: 'window' opens in new browser window, 'embedded' shows overlay on main screen",
    )

    # Preferred slicer application
    preferred_slicer: str = Field(
        default="bambu_studio",
        description="Preferred slicer: 'bambu_studio' or 'orcaslicer'",
    )

    # Prometheus metrics endpoint
    prometheus_enabled: bool = Field(default=False, description="Enable Prometheus metrics endpoint at /metrics")
    prometheus_token: str = Field(
        default="", description="Bearer token for Prometheus metrics authentication (optional)"
    )

    # Inventory low stock threshold
    low_stock_threshold: float = Field(
        default=20.0,
        ge=0.1,
        le=99.9,
        description="Low stock threshold percentage (%) for inventory filtering and display",
    )

    # User email notifications (requires Advanced Authentication)
    user_notifications_enabled: bool = Field(
        default=True,
        description="Enable user email notifications for print job events (requires Advanced Authentication)",
    )

    # Default print options
    default_bed_levelling: bool = Field(default=True, description="Default bed levelling option for new prints")
    default_flow_cali: bool = Field(default=False, description="Default flow calibration option for new prints")
    default_vibration_cali: bool = Field(
        default=True, description="Default vibration calibration option for new prints"
    )
    default_layer_inspect: bool = Field(
        default=False, description="Default first layer inspection option for new prints"
    )
    default_timelapse: bool = Field(default=False, description="Default timelapse option for new prints")

    # Staggered batch start for multi-printer jobs
    stagger_group_size: int = Field(
        default=2, ge=1, le=50, description="Number of printers to start simultaneously in staggered mode"
    )
    stagger_interval_minutes: int = Field(
        default=5, ge=1, le=60, description="Minutes between staggered printer groups"
    )

    # Plate-clear confirmation for queue scheduling
    require_plate_clear: bool = Field(
        default=True,
        description="Require per-printer plate-clear confirmation before starting queued prints on finished printers",
    )
    queue_shortest_first: bool = Field(
        default=False,
        description="Shortest Job First — scheduler prioritizes shorter print jobs over longer ones",
    )

    # LDAP authentication (#794)
    ldap_enabled: bool = Field(default=False, description="Enable LDAP authentication")
    ldap_server_url: str = Field(default="", description="LDAP server URL (e.g., ldap://ldap.example.com:389)")
    ldap_bind_dn: str = Field(default="", description="Bind DN for LDAP searches (e.g., cn=admin,dc=example,dc=com)")
    ldap_bind_password: str = Field(default="", description="Bind password for LDAP searches")
    ldap_search_base: str = Field(default="", description="Search base DN (e.g., ou=users,dc=example,dc=com)")
    ldap_user_filter: str = Field(
        default="(sAMAccountName={username})",
        description="LDAP user search filter. {username} is replaced with the login username",
    )
    ldap_security: str = Field(default="starttls", description="LDAP security: 'starttls' or 'ldaps'")
    ldap_group_mapping: str = Field(
        default="",
        description="JSON: LDAP group to BamBuddy group mapping {ldap_group_dn: bambuddy_group_name}",
    )
    ldap_auto_provision: bool = Field(
        default=False,
        description="Auto-create BamBuddy user on first successful LDAP login",
    )
    ldap_default_group: str = Field(
        default="",
        description="Fallback BamBuddy group name assigned when an LDAP user authenticates but has no mapped groups. Empty = no fallback.",
    )

    # Default sidebar order (admin-set for all users)
    default_sidebar_order: str = Field(
        default="",
        description="JSON object with 'order' key containing array of sidebar item IDs (empty = no default)",
    )


class AppSettingsUpdate(BaseModel):
    """Schema for updating settings (all fields optional)."""

    auto_archive: bool | None = None
    save_thumbnails: bool | None = None
    capture_finish_photo: bool | None = None
    default_filament_cost: float | None = None
    currency: str | None = None
    energy_cost_per_kwh: float | None = None
    energy_tracking_mode: str | None = None
    spoolman_enabled: bool | None = None
    spoolman_url: str | None = None
    spoolman_sync_mode: str | None = None
    spoolman_disable_weight_sync: bool | None = None
    spoolman_report_partial_usage: bool | None = None
    disable_filament_warnings: bool | None = None
    prefer_lowest_filament: bool | None = None
    check_updates: bool | None = None
    check_printer_firmware: bool | None = None
    include_beta_updates: bool | None = None
    language: str | None = None
    notification_language: str | None = None
    bed_cooled_threshold: float | None = None
    ams_humidity_good: int | None = None
    ams_humidity_fair: int | None = None
    ams_temp_good: float | None = None
    ams_temp_fair: float | None = None
    ams_history_retention_days: int | None = None
    queue_drying_enabled: bool | None = None
    queue_drying_block: bool | None = None
    ambient_drying_enabled: bool | None = None
    drying_presets: str | None = None
    per_printer_mapping_expanded: bool | None = None
    date_format: str | None = None
    time_format: str | None = None
    default_printer_id: int | None = None
    virtual_printer_enabled: bool | None = None
    virtual_printer_access_code: str | None = None
    virtual_printer_mode: str | None = None
    dark_style: str | None = None
    dark_background: str | None = None
    dark_accent: str | None = None
    light_style: str | None = None
    light_background: str | None = None
    light_accent: str | None = None
    ftp_retry_enabled: bool | None = None
    ftp_retry_count: int | None = None
    ftp_retry_delay: int | None = None
    ftp_timeout: int | None = None
    mqtt_enabled: bool | None = None
    mqtt_broker: str | None = None
    mqtt_port: int | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str | None = None
    mqtt_use_tls: bool | None = None
    external_url: str | None = None
    ha_enabled: bool | None = None
    ha_url: str | None = None
    ha_token: str | None = None
    library_archive_mode: str | None = None
    library_disk_warning_gb: float | None = None
    camera_view_mode: str | None = None
    preferred_slicer: str | None = None
    prometheus_enabled: bool | None = None
    prometheus_token: str | None = None
    low_stock_threshold: float | None = Field(default=None, ge=0.1, le=99.9)
    user_notifications_enabled: bool | None = None
    default_bed_levelling: bool | None = None
    default_flow_cali: bool | None = None
    default_vibration_cali: bool | None = None
    default_layer_inspect: bool | None = None
    default_timelapse: bool | None = None
    stagger_group_size: int | None = Field(default=None, ge=1, le=50)
    stagger_interval_minutes: int | None = Field(default=None, ge=1, le=60)
    require_plate_clear: bool | None = None
    queue_shortest_first: bool | None = None
    gcode_snippets: str | None = None
    local_backup_enabled: bool | None = None
    local_backup_schedule: str | None = None
    local_backup_time: str | None = None
    local_backup_retention: int | None = None
    local_backup_path: str | None = None
    ldap_enabled: bool | None = None
    ldap_server_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_search_base: str | None = None
    ldap_user_filter: str | None = None
    ldap_security: str | None = None
    ldap_group_mapping: str | None = None
    ldap_auto_provision: bool | None = None
    ldap_default_group: str | None = None
    default_sidebar_order: str | None = None

    @field_validator("gcode_snippets")
    @classmethod
    def validate_gcode_snippets(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            raise ValueError("gcode_snippets must be valid JSON or empty")
        if not isinstance(parsed, dict):
            raise ValueError("gcode_snippets must be a JSON object keyed by printer model")
        return v

    @field_validator("ldap_group_mapping")
    @classmethod
    def validate_ldap_group_mapping(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            raise ValueError("ldap_group_mapping must be valid JSON or empty")
        if not isinstance(parsed, dict):
            raise ValueError("ldap_group_mapping must be a JSON object mapping LDAP group DNs to BamBuddy group names")
        return v

    @field_validator("default_sidebar_order")
    @classmethod
    def validate_default_sidebar_order(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            raise ValueError("default_sidebar_order must be valid JSON or empty")
        if isinstance(parsed, dict):
            order = parsed.get("order")
        elif isinstance(parsed, list):
            order = parsed
        else:
            raise ValueError("default_sidebar_order must be a JSON object with 'order' key or a JSON array")
        if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
            raise ValueError("sidebar order must be an array of strings")
        return v
