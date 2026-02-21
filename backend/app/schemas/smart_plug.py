from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SmartPlugBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    plug_type: Literal["tasmota", "homeassistant", "mqtt", "rest"] = "tasmota"

    # Tasmota fields (required when plug_type="tasmota")
    ip_address: str | None = Field(default=None, pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    username: str | None = None
    password: str | None = None

    # Home Assistant fields (required when plug_type="homeassistant")
    ha_entity_id: str | None = Field(default=None, pattern=r"^(switch|light|input_boolean|script)\.[a-z0-9_]+$")
    # Home Assistant energy sensor entities (optional, for separate energy sensors)
    ha_power_entity: str | None = Field(default=None, pattern=r"^sensor\.[a-z0-9_]+$")
    ha_energy_today_entity: str | None = Field(default=None, pattern=r"^sensor\.[a-z0-9_]+$")
    ha_energy_total_entity: str | None = Field(default=None, pattern=r"^sensor\.[a-z0-9_]+$")

    # MQTT fields (required when plug_type="mqtt")
    # Legacy field - kept for backward compatibility
    mqtt_topic: str | None = Field(default=None, max_length=200)  # Deprecated, use mqtt_power_topic

    # Power monitoring
    mqtt_power_topic: str | None = Field(default=None, max_length=200)  # Topic for power data
    mqtt_power_path: str | None = Field(default=None, max_length=100)  # e.g., "power_l1" or "data.power"
    mqtt_power_multiplier: float = Field(default=1.0, ge=0.0001, le=10000)  # Unit conversion for power

    # Energy monitoring
    mqtt_energy_topic: str | None = Field(default=None, max_length=200)  # Topic for energy data
    mqtt_energy_path: str | None = Field(default=None, max_length=100)  # e.g., "energy_l1"
    mqtt_energy_multiplier: float = Field(default=1.0, ge=0.0001, le=10000)  # Unit conversion for energy

    # State monitoring
    mqtt_state_topic: str | None = Field(default=None, max_length=200)  # Topic for state data
    mqtt_state_path: str | None = Field(default=None, max_length=100)  # e.g., "state_l1" for ON/OFF
    mqtt_state_on_value: str | None = Field(
        default=None, max_length=50
    )  # What value means "ON" (e.g., "ON", "true", "1")

    # REST API plug fields (required when plug_type="rest")
    # User-defined URLs for each action; GET or POST method is configurable
    rest_on_url: str | None = Field(default=None, max_length=500)      # URL to turn on
    rest_off_url: str | None = Field(default=None, max_length=500)     # URL to turn off
    rest_toggle_url: str | None = Field(default=None, max_length=500)  # URL to toggle (optional)
    rest_status_url: str | None = Field(default=None, max_length=500)  # URL to query status (optional)
    rest_method: Literal["GET", "POST"] = "GET"                        # HTTP method to use
    rest_state_on_value: str | None = Field(default=None, max_length=100)  # Substring in response body meaning ON

    # Legacy multiplier - kept for backward compatibility
    mqtt_multiplier: float = Field(default=1.0, ge=0.0001, le=10000)  # Deprecated, use mqtt_power_multiplier

    printer_id: int | None = None
    enabled: bool = True
    auto_on: bool = True
    auto_off: bool = True
    off_delay_mode: Literal["time", "temperature"] = "time"
    off_delay_minutes: int = Field(default=5, ge=0, le=60)
    off_temp_threshold: int = Field(default=70, ge=30, le=150)
    # Power alerts
    power_alert_enabled: bool = False
    power_alert_high: float | None = Field(default=None, ge=0, le=5000)  # Alert when power > this (watts)
    power_alert_low: float | None = Field(default=None, ge=0, le=5000)  # Alert when power < this (watts)
    # Schedule
    schedule_enabled: bool = False
    schedule_on_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM format
    schedule_off_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM format
    # Visibility options
    show_in_switchbar: bool = False
    show_on_printer_card: bool = True  # For scripts: show on printer card

    @model_validator(mode="after")
    def validate_plug_type_fields(self) -> "SmartPlugBase":
        if self.plug_type == "tasmota" and not self.ip_address:
            raise ValueError("ip_address is required for Tasmota plugs")
        if self.plug_type == "homeassistant" and not self.ha_entity_id:
            raise ValueError("ha_entity_id is required for Home Assistant plugs")
        if self.plug_type == "rest":
            if not self.rest_on_url and not self.rest_off_url and not self.rest_status_url:
                raise ValueError("At least one URL (on_url, off_url, or status_url) must be configured for REST plugs")
        if self.plug_type == "mqtt":
            # Determine the effective power topic (new field takes priority, fall back to legacy)
            power_topic = self.mqtt_power_topic or self.mqtt_topic
            # Path is optional - if not set, raw MQTT payload value will be used
            has_power = bool(power_topic)
            has_energy = bool(self.mqtt_energy_topic)
            has_state = bool(self.mqtt_state_topic)

            # At least one data source must be configured (path is optional)
            if not has_power and not has_energy and not has_state:
                raise ValueError("At least one MQTT topic must be configured for power, energy, or state monitoring")
        return self


class SmartPlugCreate(SmartPlugBase):
    pass


class SmartPlugUpdate(BaseModel):
    name: str | None = None
    plug_type: Literal["tasmota", "homeassistant", "mqtt", "rest"] | None = None
    ip_address: str | None = None
    ha_entity_id: str | None = None
    # Home Assistant energy sensor entities (optional)
    ha_power_entity: str | None = None
    ha_energy_today_entity: str | None = None
    ha_energy_total_entity: str | None = None
    # MQTT fields (legacy)
    mqtt_topic: str | None = None
    mqtt_multiplier: float | None = Field(default=None, ge=0.0001, le=10000)
    # MQTT power fields
    mqtt_power_topic: str | None = None
    mqtt_power_path: str | None = None
    mqtt_power_multiplier: float | None = Field(default=None, ge=0.0001, le=10000)
    # MQTT energy fields
    mqtt_energy_topic: str | None = None
    mqtt_energy_path: str | None = None
    mqtt_energy_multiplier: float | None = Field(default=None, ge=0.0001, le=10000)
    # MQTT state fields
    mqtt_state_topic: str | None = None
    mqtt_state_path: str | None = None
    mqtt_state_on_value: str | None = None

    # REST API plug fields
    rest_on_url: str | None = None
    rest_off_url: str | None = None
    rest_toggle_url: str | None = None
    rest_status_url: str | None = None
    rest_method: Literal["GET", "POST"] | None = None
    rest_state_on_value: str | None = None
    printer_id: int | None = None
    enabled: bool | None = None
    auto_on: bool | None = None
    auto_off: bool | None = None
    off_delay_mode: Literal["time", "temperature"] | None = None
    off_delay_minutes: int | None = Field(default=None, ge=0, le=60)
    off_temp_threshold: int | None = Field(default=None, ge=30, le=150)
    username: str | None = None
    password: str | None = None
    # Power alerts
    power_alert_enabled: bool | None = None
    power_alert_high: float | None = Field(default=None, ge=0, le=5000)
    power_alert_low: float | None = Field(default=None, ge=0, le=5000)
    # Schedule
    schedule_enabled: bool | None = None
    schedule_on_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule_off_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # Visibility options
    show_in_switchbar: bool | None = None
    show_on_printer_card: bool | None = None


class SmartPlugResponse(SmartPlugBase):
    id: int
    last_state: str | None = None
    last_checked: datetime | None = None
    auto_off_executed: bool = False  # True when auto-off was triggered after print
    power_alert_last_triggered: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SmartPlugControl(BaseModel):
    action: Literal["on", "off", "toggle"]


class SmartPlugEnergy(BaseModel):
    """Energy monitoring data from a smart plug."""

    power: float | None = None  # Current watts
    voltage: float | None = None  # Volts
    current: float | None = None  # Amps
    today: float | None = None  # kWh used today
    yesterday: float | None = None  # kWh used yesterday
    total: float | None = None  # Total kWh
    factor: float | None = None  # Power factor (0-1)
    apparent_power: float | None = None  # VA
    reactive_power: float | None = None  # VAr


class SmartPlugStatus(BaseModel):
    state: str | None = None  # "ON", "OFF", or None if unreachable
    reachable: bool = True
    device_name: str | None = None
    energy: SmartPlugEnergy | None = None  # Energy data if available


class SmartPlugTestConnection(BaseModel):
    ip_address: str = Field(..., pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    username: str | None = None
    password: str | None = None


# Home Assistant schemas
class HATestConnectionRequest(BaseModel):
    """Request to test Home Assistant connection."""

    url: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)


class HATestConnectionResponse(BaseModel):
    """Response from HA connection test."""

    success: bool
    message: str | None = None
    error: str | None = None


class HAEntity(BaseModel):
    """A Home Assistant entity that can be used as a smart plug."""

    entity_id: str
    friendly_name: str
    state: str | None = None
    domain: str  # "switch", "light", "input_boolean", "script"


class HASensorEntity(BaseModel):
    """A Home Assistant sensor entity for energy monitoring."""

    entity_id: str
    friendly_name: str
    state: str | None = None
    unit_of_measurement: str | None = None  # "W", "kW", "kWh", "Wh"
