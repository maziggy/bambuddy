"""Initial schema for MySQL - matches current SQLite schema after all inline migrations.

Revision ID: 0001
Revises:
Create Date: 2026-02-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- printers ---
    op.create_table(
        "printers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("serial_number", sa.String(50), unique=True, nullable=False),
        sa.Column("ip_address", sa.String(253), nullable=False),
        sa.Column("access_code", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column("nozzle_count", sa.Integer, server_default="1"),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("1")),
        sa.Column("auto_archive", sa.Boolean, server_default=sa.text("1")),
        sa.Column("print_hours_offset", sa.Float, server_default="0.0"),
        sa.Column("runtime_seconds", sa.Integer, server_default="0"),
        sa.Column("last_runtime_update", sa.DateTime, nullable=True),
        sa.Column("external_camera_url", sa.String(500), nullable=True),
        sa.Column("external_camera_type", sa.String(20), nullable=True),
        sa.Column("external_camera_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("plate_detection_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("plate_detection_roi_x", sa.Float, nullable=True),
        sa.Column("plate_detection_roi_y", sa.Float, nullable=True),
        sa.Column("plate_detection_roi_w", sa.Float, nullable=True),
        sa.Column("plate_detection_roi_h", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("color", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("target_count", sa.Integer, nullable=True),
        sa.Column("target_parts_count", sa.Integer, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("attachments", sa.JSON, nullable=True),
        sa.Column("tags", sa.Text, nullable=True),
        sa.Column("due_date", sa.DateTime, nullable=True),
        sa.Column("priority", sa.String(20), server_default="normal"),
        sa.Column("budget", sa.Float, nullable=True),
        sa.Column("is_template", sa.Boolean, server_default=sa.text("0")),
        sa.Column("template_source_id", sa.Integer, nullable=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), server_default="user"),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # --- groups ---
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("permissions", sa.JSON, nullable=True),
        sa.Column("is_system", sa.Boolean, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_groups_name", "groups", ["name"])

    # --- user_groups ---
    op.create_table(
        "user_groups",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", sa.Integer, sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    )

    # --- print_archives ---
    op.create_table(
        "print_archives",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id"), nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("thumbnail_path", sa.String(500), nullable=True),
        sa.Column("timelapse_path", sa.String(500), nullable=True),
        sa.Column("source_3mf_path", sa.String(500), nullable=True),
        sa.Column("f3d_path", sa.String(500), nullable=True),
        sa.Column("print_name", sa.String(255), nullable=True),
        sa.Column("print_time_seconds", sa.Integer, nullable=True),
        sa.Column("filament_used_grams", sa.Float, nullable=True),
        sa.Column("filament_type", sa.String(50), nullable=True),
        sa.Column("filament_color", sa.String(50), nullable=True),
        sa.Column("layer_height", sa.Float, nullable=True),
        sa.Column("total_layers", sa.Integer, nullable=True),
        sa.Column("nozzle_diameter", sa.Float, nullable=True),
        sa.Column("bed_temperature", sa.Integer, nullable=True),
        sa.Column("nozzle_temperature", sa.Integer, nullable=True),
        sa.Column("sliced_for_model", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), server_default="completed"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("extra_data", sa.JSON, nullable=True),
        sa.Column("makerworld_url", sa.String(500), nullable=True),
        sa.Column("designer", sa.String(255), nullable=True),
        sa.Column("external_url", sa.String(500), nullable=True),
        sa.Column("is_favorite", sa.Boolean, server_default=sa.text("0")),
        sa.Column("tags", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("cost", sa.Float, nullable=True),
        sa.Column("photos", sa.JSON, nullable=True),
        sa.Column("failure_reason", sa.String(100), nullable=True),
        sa.Column("quantity", sa.Integer, server_default="1"),
        sa.Column("energy_kwh", sa.Float, nullable=True),
        sa.Column("energy_cost", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    # MySQL FULLTEXT index — replaces SQLite FTS5 virtual table
    op.execute(
        "CREATE FULLTEXT INDEX ft_archives_search ON print_archives"
        "(print_name, filename, tags, notes, designer, filament_type)"
    )

    # --- library_folders ---
    op.create_table(
        "library_folders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("library_folders.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_external", sa.Boolean, server_default=sa.text("0")),
        sa.Column("external_readonly", sa.Boolean, server_default=sa.text("0")),
        sa.Column("external_show_hidden", sa.Boolean, server_default=sa.text("0")),
        sa.Column("external_path", sa.String(500), nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("archive_id", sa.Integer, sa.ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- library_files ---
    op.create_table(
        "library_files",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("folder_id", sa.Integer, sa.ForeignKey("library_folders.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_external", sa.Boolean, server_default=sa.text("0")),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("thumbnail_path", sa.String(500), nullable=True),
        sa.Column("file_metadata", sa.JSON, nullable=True),
        sa.Column("print_count", sa.Integer, server_default="0"),
        sa.Column("last_printed_at", sa.DateTime, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- print_queue ---
    op.create_table(
        "print_queue",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("target_model", sa.String(50), nullable=True),
        sa.Column("target_location", sa.String(100), nullable=True),
        sa.Column("required_filament_types", sa.Text, nullable=True),
        sa.Column("waiting_reason", sa.Text, nullable=True),
        sa.Column("archive_id", sa.Integer, sa.ForeignKey("print_archives.id", ondelete="CASCADE"), nullable=True),
        sa.Column("library_file_id", sa.Integer, sa.ForeignKey("library_files.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("position", sa.Integer, server_default="0"),
        sa.Column("scheduled_time", sa.DateTime, nullable=True),
        sa.Column("manual_start", sa.Boolean, server_default=sa.text("0")),
        sa.Column("require_previous_success", sa.Boolean, server_default=sa.text("0")),
        sa.Column("auto_off_after", sa.Boolean, server_default=sa.text("0")),
        sa.Column("ams_mapping", sa.Text, nullable=True),
        sa.Column("plate_id", sa.Integer, nullable=True),
        sa.Column("bed_levelling", sa.Boolean, server_default=sa.text("1")),
        sa.Column("flow_cali", sa.Boolean, server_default=sa.text("0")),
        sa.Column("vibration_cali", sa.Boolean, server_default=sa.text("1")),
        sa.Column("layer_inspect", sa.Boolean, server_default=sa.text("0")),
        sa.Column("timelapse", sa.Boolean, server_default=sa.text("0")),
        sa.Column("use_ams", sa.Boolean, server_default=sa.text("1")),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- smart_plugs ---
    op.create_table(
        "smart_plugs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("plug_type", sa.String(20), server_default="tasmota"),
        sa.Column("ha_entity_id", sa.String(100), nullable=True),
        sa.Column("ha_power_entity", sa.String(100), nullable=True),
        sa.Column("ha_energy_today_entity", sa.String(100), nullable=True),
        sa.Column("ha_energy_total_entity", sa.String(100), nullable=True),
        sa.Column("mqtt_topic", sa.String(200), nullable=True),
        sa.Column("mqtt_power_topic", sa.String(200), nullable=True),
        sa.Column("mqtt_power_path", sa.String(100), nullable=True),
        sa.Column("mqtt_power_multiplier", sa.Float, server_default="1.0"),
        sa.Column("mqtt_energy_topic", sa.String(200), nullable=True),
        sa.Column("mqtt_energy_path", sa.String(100), nullable=True),
        sa.Column("mqtt_energy_multiplier", sa.Float, server_default="1.0"),
        sa.Column("mqtt_state_topic", sa.String(200), nullable=True),
        sa.Column("mqtt_state_path", sa.String(100), nullable=True),
        sa.Column("mqtt_state_on_value", sa.String(50), nullable=True),
        sa.Column("mqtt_multiplier", sa.Float, server_default="1.0"),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("1")),
        sa.Column("auto_on", sa.Boolean, server_default=sa.text("1")),
        sa.Column("auto_off", sa.Boolean, server_default=sa.text("1")),
        sa.Column("off_delay_mode", sa.String(20), server_default="time"),
        sa.Column("off_delay_minutes", sa.Integer, server_default="5"),
        sa.Column("off_temp_threshold", sa.Integer, server_default="70"),
        sa.Column("username", sa.String(50), nullable=True),
        sa.Column("password", sa.String(100), nullable=True),
        sa.Column("power_alert_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("power_alert_high", sa.Float, nullable=True),
        sa.Column("power_alert_low", sa.Float, nullable=True),
        sa.Column("power_alert_last_triggered", sa.DateTime, nullable=True),
        sa.Column("schedule_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("schedule_on_time", sa.String(5), nullable=True),
        sa.Column("schedule_off_time", sa.String(5), nullable=True),
        sa.Column("show_in_switchbar", sa.Boolean, server_default=sa.text("0")),
        sa.Column("show_on_printer_card", sa.Boolean, server_default=sa.text("1")),
        sa.Column("last_state", sa.String(10), nullable=True),
        sa.Column("last_checked", sa.DateTime, nullable=True),
        sa.Column("auto_off_executed", sa.Boolean, server_default=sa.text("0")),
        sa.Column("auto_off_pending", sa.Boolean, server_default=sa.text("0")),
        sa.Column("auto_off_pending_since", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- notification_providers ---
    op.create_table(
        "notification_providers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("1")),
        sa.Column("config", sa.Text, nullable=False),
        sa.Column("on_print_start", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_print_complete", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_print_failed", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_print_stopped", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_print_progress", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_printer_offline", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_printer_error", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_filament_low", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_maintenance_due", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_ams_humidity_high", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_ams_temperature_high", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_ams_ht_humidity_high", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_ams_ht_temperature_high", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_plate_not_empty", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_bed_cooled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_queue_job_added", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_queue_job_assigned", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_queue_job_started", sa.Boolean, server_default=sa.text("0")),
        sa.Column("on_queue_job_waiting", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_queue_job_skipped", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_queue_job_failed", sa.Boolean, server_default=sa.text("1")),
        sa.Column("on_queue_completed", sa.Boolean, server_default=sa.text("0")),
        sa.Column("quiet_hours_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("quiet_hours_start", sa.String(5), nullable=True),
        sa.Column("quiet_hours_end", sa.String(5), nullable=True),
        sa.Column("daily_digest_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("daily_digest_time", sa.String(5), nullable=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_success", sa.DateTime, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_error_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- notification_logs ---
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "provider_id",
            sa.Integer,
            sa.ForeignKey("notification_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("success", sa.Boolean, server_default=sa.text("1")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("printer_name", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_notification_logs_created_at", "notification_logs", ["created_at"])

    # --- notification_digest_queue ---
    op.create_table(
        "notification_digest_queue",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "provider_id",
            sa.Integer,
            sa.ForeignKey("notification_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("printer_name", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_notification_digest_queue_created_at", "notification_digest_queue", ["created_at"])

    # --- notification_templates ---
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("title_template", sa.Text, nullable=False),
        sa.Column("body_template", sa.Text, nullable=False),
        sa.Column("is_default", sa.Boolean, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- filaments ---
    op.create_table(
        "filaments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("color", sa.String(50), nullable=True),
        sa.Column("color_hex", sa.String(7), nullable=True),
        sa.Column("cost_per_kg", sa.Float, server_default="25.0"),
        sa.Column("spool_weight_g", sa.Float, server_default="1000.0"),
        sa.Column("currency", sa.String(3), server_default="USD"),
        sa.Column("density", sa.Float, nullable=True),
        sa.Column("print_temp_min", sa.Integer, nullable=True),
        sa.Column("print_temp_max", sa.Integer, nullable=True),
        sa.Column("bed_temp_min", sa.Integer, nullable=True),
        sa.Column("bed_temp_max", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- settings ---
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(100), unique=True, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_settings_key", "settings", ["key"])

    # --- maintenance_types ---
    op.create_table(
        "maintenance_types",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("default_interval_hours", sa.Float, server_default="100.0"),
        sa.Column("interval_type", sa.String(20), server_default="hours"),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("wiki_url", sa.String(500), nullable=True),
        sa.Column("is_system", sa.Boolean, server_default=sa.text("0")),
        sa.Column("is_deleted", sa.Boolean, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- printer_maintenance ---
    op.create_table(
        "printer_maintenance",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "maintenance_type_id",
            sa.Integer,
            sa.ForeignKey("maintenance_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("custom_interval_hours", sa.Float, nullable=True),
        sa.Column("custom_interval_type", sa.String(20), nullable=True),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("1")),
        sa.Column("last_performed_at", sa.DateTime, nullable=True),
        sa.Column("last_performed_hours", sa.Float, server_default="0.0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- maintenance_history ---
    op.create_table(
        "maintenance_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "printer_maintenance_id",
            sa.Integer,
            sa.ForeignKey("printer_maintenance.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("performed_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("hours_at_maintenance", sa.Float, server_default="0.0"),
        sa.Column("notes", sa.Text, nullable=True),
    )

    # --- api_keys ---
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("can_queue", sa.Boolean, server_default=sa.text("1")),
        sa.Column("can_control_printer", sa.Boolean, server_default=sa.text("0")),
        sa.Column("can_read_status", sa.Boolean, server_default=sa.text("1")),
        sa.Column("printer_ids", sa.JSON, nullable=True),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("1")),
        sa.Column("last_used", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime, nullable=True),
    )

    # --- spool ---
    op.create_table(
        "spool",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("material", sa.String(50), nullable=False),
        sa.Column("subtype", sa.String(50), nullable=True),
        sa.Column("color_name", sa.String(100), nullable=True),
        sa.Column("rgba", sa.String(8), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("label_weight", sa.Integer, server_default="1000"),
        sa.Column("core_weight", sa.Integer, server_default="250"),
        sa.Column("weight_used", sa.Float, server_default="0"),
        sa.Column("slicer_filament", sa.String(50), nullable=True),
        sa.Column("slicer_filament_name", sa.String(100), nullable=True),
        sa.Column("nozzle_temp_min", sa.Integer, nullable=True),
        sa.Column("nozzle_temp_max", sa.Integer, nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("added_full", sa.Boolean, nullable=True),
        sa.Column("last_used", sa.DateTime, nullable=True),
        sa.Column("encode_time", sa.DateTime, nullable=True),
        sa.Column("tag_uid", sa.String(16), nullable=True),
        sa.Column("tray_uuid", sa.String(32), nullable=True),
        sa.Column("data_origin", sa.String(20), nullable=True),
        sa.Column("tag_type", sa.String(20), nullable=True),
        sa.Column("archived_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- spool_assignment ---
    op.create_table(
        "spool_assignment",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("spool_id", sa.Integer, sa.ForeignKey("spool.id", ondelete="CASCADE"), nullable=False),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ams_id", sa.Integer, nullable=False),
        sa.Column("tray_id", sa.Integer, nullable=False),
        sa.Column("fingerprint_color", sa.String(8), nullable=True),
        sa.Column("fingerprint_type", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("printer_id", "ams_id", "tray_id"),
    )

    # --- spool_k_profile ---
    op.create_table(
        "spool_k_profile",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("spool_id", sa.Integer, sa.ForeignKey("spool.id", ondelete="CASCADE"), nullable=False),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extruder", sa.Integer, server_default="0"),
        sa.Column("nozzle_diameter", sa.String(10), server_default="0.4"),
        sa.Column("nozzle_type", sa.String(50), nullable=True),
        sa.Column("k_value", sa.Float, nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("cali_idx", sa.Integer, nullable=True),
        sa.Column("setting_id", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- spool_catalog ---
    op.create_table(
        "spool_catalog",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("weight", sa.Integer, nullable=False),
        sa.Column("is_default", sa.Boolean, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- spool_usage_history ---
    op.create_table(
        "spool_usage_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("spool_id", sa.Integer, sa.ForeignKey("spool.id", ondelete="CASCADE"), nullable=False),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("print_name", sa.String(500), nullable=True),
        sa.Column("weight_used", sa.Float, server_default="0"),
        sa.Column("percent_used", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="completed"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- color_catalog ---
    op.create_table(
        "color_catalog",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("manufacturer", sa.String(200), nullable=False),
        sa.Column("color_name", sa.String(200), nullable=False),
        sa.Column("hex_color", sa.String(7), nullable=False),
        sa.Column("material", sa.String(100), nullable=True),
        sa.Column("is_default", sa.Boolean, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- project_bom_items ---
    op.create_table(
        "project_bom_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("quantity_needed", sa.Integer, server_default="1"),
        sa.Column("quantity_acquired", sa.Integer, server_default="0"),
        sa.Column("unit_price", sa.Float, nullable=True),
        sa.Column("sourcing_url", sa.String(512), nullable=True),
        sa.Column("archive_id", sa.Integer, sa.ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stl_filename", sa.String(255), nullable=True),
        sa.Column("remarks", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- active_print_spoolman ---
    op.create_table(
        "active_print_spoolman",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "archive_id",
            sa.Integer,
            sa.ForeignKey("print_archives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filament_usage", sa.JSON, nullable=False),
        sa.Column("ams_trays", sa.JSON, nullable=False),
        sa.Column("slot_to_tray", sa.JSON, nullable=True),
        sa.Column("layer_usage", sa.JSON, nullable=True),
        sa.Column("filament_properties", sa.JSON, nullable=True),
        sa.UniqueConstraint("printer_id", "archive_id", name="uq_printer_archive"),
    )

    # --- slot_preset_mappings ---
    op.create_table(
        "slot_preset_mappings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ams_id", sa.Integer, nullable=False),
        sa.Column("tray_id", sa.Integer, nullable=False),
        sa.Column("preset_id", sa.String(100), nullable=False),
        sa.Column("preset_name", sa.String(200), nullable=False),
        sa.Column("preset_source", sa.String(20), server_default="cloud"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("printer_id", "ams_id", "tray_id", name="uq_slot_preset"),
    )

    # --- local_presets ---
    op.create_table(
        "local_presets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("preset_type", sa.String(20), nullable=False),
        sa.Column("source", sa.String(50), server_default="orcaslicer"),
        sa.Column("filament_type", sa.String(50), nullable=True),
        sa.Column("filament_vendor", sa.String(200), nullable=True),
        sa.Column("nozzle_temp_min", sa.Integer, nullable=True),
        sa.Column("nozzle_temp_max", sa.Integer, nullable=True),
        sa.Column("pressure_advance", sa.String(50), nullable=True),
        sa.Column("default_filament_colour", sa.String(50), nullable=True),
        sa.Column("filament_cost", sa.String(50), nullable=True),
        sa.Column("filament_density", sa.String(50), nullable=True),
        sa.Column("compatible_printers", sa.Text, nullable=True),
        sa.Column("setting", sa.Text, nullable=False),
        sa.Column("inherits", sa.String(300), nullable=True),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- orca_base_profiles ---
    op.create_table(
        "orca_base_profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("profile_type", sa.String(20), nullable=False),
        sa.Column("setting", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_orca_base_profiles_name", "orca_base_profiles", ["name"], unique=True)

    # --- pending_uploads ---
    op.create_table(
        "pending_uploads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("tags", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("archived_id", sa.Integer, sa.ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime, nullable=True),
    )

    # --- print_log_entries ---
    op.create_table(
        "print_log_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("print_name", sa.String(255), nullable=True),
        sa.Column("printer_name", sa.String(255), nullable=True),
        sa.Column("printer_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("filament_type", sa.String(50), nullable=True),
        sa.Column("filament_color", sa.String(50), nullable=True),
        sa.Column("filament_used_grams", sa.Float, nullable=True),
        sa.Column("thumbnail_path", sa.String(500), nullable=True),
        sa.Column("created_by_username", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- external_links ---
    op.create_table(
        "external_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("icon", sa.String(50), server_default="link"),
        sa.Column("custom_icon", sa.String(255), nullable=True),
        sa.Column("open_in_new_tab", sa.Boolean, server_default=sa.text("0")),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- kprofile_notes ---
    op.create_table(
        "kprofile_notes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("setting_id", sa.String(100), nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_kprofile_notes_printer_setting", "kprofile_notes", ["printer_id", "setting_id"], unique=True)

    # --- ams_sensor_history ---
    op.create_table(
        "ams_sensor_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.Integer, sa.ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ams_id", sa.Integer, nullable=False),
        sa.Column("humidity", sa.Float, nullable=True),
        sa.Column("humidity_raw", sa.Float, nullable=True),
        sa.Column("temperature", sa.Float, nullable=True),
        sa.Column("recorded_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_ams_sensor_history_printer_id", "ams_sensor_history", ["printer_id"])
    op.create_index(
        "ix_ams_history_printer_ams_time",
        "ams_sensor_history",
        ["printer_id", "ams_id", "recorded_at"],
    )

    # --- github_backup_config ---
    op.create_table(
        "github_backup_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("repository_url", sa.String(500), nullable=False),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("branch", sa.String(100), server_default="main"),
        sa.Column("schedule_enabled", sa.Boolean, server_default=sa.text("0")),
        sa.Column("schedule_type", sa.String(20), server_default="daily"),
        sa.Column("schedule_cron", sa.String(100), nullable=True),
        sa.Column("backup_kprofiles", sa.Boolean, server_default=sa.text("1")),
        sa.Column("backup_cloud_profiles", sa.Boolean, server_default=sa.text("1")),
        sa.Column("backup_settings", sa.Boolean, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("1")),
        sa.Column("last_backup_at", sa.DateTime, nullable=True),
        sa.Column("last_backup_status", sa.String(20), nullable=True),
        sa.Column("last_backup_message", sa.Text, nullable=True),
        sa.Column("last_backup_commit_sha", sa.String(40), nullable=True),
        sa.Column("next_scheduled_run", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # --- github_backup_logs ---
    op.create_table(
        "github_backup_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "config_id",
            sa.Integer,
            sa.ForeignKey("github_backup_config.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("files_changed", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
    )


def downgrade() -> None:
    # Drop all tables in reverse dependency order
    op.drop_table("github_backup_logs")
    op.drop_table("github_backup_config")
    op.drop_table("ams_sensor_history")
    op.drop_table("kprofile_notes")
    op.drop_table("external_links")
    op.drop_table("print_log_entries")
    op.drop_table("pending_uploads")
    op.drop_table("orca_base_profiles")
    op.drop_table("local_presets")
    op.drop_table("slot_preset_mappings")
    op.drop_table("active_print_spoolman")
    op.drop_table("project_bom_items")
    op.drop_table("color_catalog")
    op.drop_table("spool_usage_history")
    op.drop_table("spool_catalog")
    op.drop_table("spool_k_profile")
    op.drop_table("spool_assignment")
    op.drop_table("spool")
    op.drop_table("api_keys")
    op.drop_table("maintenance_history")
    op.drop_table("printer_maintenance")
    op.drop_table("maintenance_types")
    op.drop_table("settings")
    op.drop_table("filaments")
    op.drop_table("notification_templates")
    op.drop_table("notification_digest_queue")
    op.drop_table("notification_logs")
    op.drop_table("notification_providers")
    op.drop_table("smart_plugs")
    op.drop_table("print_queue")
    op.drop_table("library_files")
    op.drop_table("library_folders")
    op.drop_table("print_archives")
    op.drop_table("user_groups")
    op.drop_table("groups")
    op.drop_table("users")
    op.drop_table("projects")
    op.drop_table("printers")
