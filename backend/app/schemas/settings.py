from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    """Application settings schema."""

    auto_archive: bool = Field(default=True, description="Automatically archive prints when completed")
    save_thumbnails: bool = Field(default=True, description="Extract and save preview images from 3MF files")
    capture_finish_photo: bool = Field(
        default=True, description="Capture photo from printer camera when print completes"
    )
    default_filament_cost: float = Field(default=25.0, description="Default filament cost per kg")
    currency: str = Field(default="USD", description="Currency for cost tracking")
    energy_cost_per_kwh: float = Field(default=0.150, description="Electricity cost per kWh for energy tracking")
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

    # Updates
    check_updates: bool = Field(default=True, description="Automatically check for updates on startup")
    check_printer_firmware: bool = Field(default=True, description="Check for printer firmware updates from Bambu Lab")
    include_beta_updates: bool = Field(default=False, description="Include beta/prerelease versions in update checks")

    # Language
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
    ftp_timeout: int = Field(default=30, description="FTP connection timeout in seconds (10-120)")

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
    check_updates: bool | None = None
    check_printer_firmware: bool | None = None
    include_beta_updates: bool | None = None
    notification_language: str | None = None
    bed_cooled_threshold: float | None = None
    ams_humidity_good: int | None = None
    ams_humidity_fair: int | None = None
    ams_temp_good: float | None = None
    ams_temp_fair: float | None = None
    ams_history_retention_days: int | None = None
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
