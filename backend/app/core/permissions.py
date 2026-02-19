"""Permission definitions for the group-based access control system.

This module defines all permissions using a string enum with `resource:action` naming.
Permissions are additive across groups - a user has all permissions from all their groups.
"""

from enum import StrEnum


class Permission(StrEnum):
    """All available permissions in the system.

    Permissions follow the pattern: resource:action
    Actions typically include: read, create, update, delete, plus resource-specific actions.
    """

    # Printers
    PRINTERS_READ = "printers:read"
    PRINTERS_CREATE = "printers:create"
    PRINTERS_UPDATE = "printers:update"
    PRINTERS_DELETE = "printers:delete"
    PRINTERS_CONTROL = "printers:control"  # Start/stop/pause/resume prints
    PRINTERS_FILES = "printers:files"  # Send files to printer
    PRINTERS_AMS_RFID = "printers:ams_rfid"  # Re-read AMS RFID tags
    PRINTERS_CLEAR_PLATE = "printers:clear_plate"  # Confirm plate cleared for next print

    # Archives
    ARCHIVES_READ = "archives:read"
    ARCHIVES_CREATE = "archives:create"
    ARCHIVES_UPDATE_OWN = "archives:update_own"
    ARCHIVES_UPDATE_ALL = "archives:update_all"
    ARCHIVES_DELETE_OWN = "archives:delete_own"
    ARCHIVES_DELETE_ALL = "archives:delete_all"
    ARCHIVES_REPRINT_OWN = "archives:reprint_own"
    ARCHIVES_REPRINT_ALL = "archives:reprint_all"

    # Queue
    QUEUE_READ = "queue:read"
    QUEUE_CREATE = "queue:create"
    QUEUE_UPDATE_OWN = "queue:update_own"
    QUEUE_UPDATE_ALL = "queue:update_all"
    QUEUE_DELETE_OWN = "queue:delete_own"
    QUEUE_DELETE_ALL = "queue:delete_all"
    QUEUE_REORDER = "queue:reorder"

    # Library
    LIBRARY_READ = "library:read"
    LIBRARY_UPLOAD = "library:upload"
    LIBRARY_UPDATE_OWN = "library:update_own"
    LIBRARY_UPDATE_ALL = "library:update_all"
    LIBRARY_DELETE_OWN = "library:delete_own"
    LIBRARY_DELETE_ALL = "library:delete_all"

    # Projects
    PROJECTS_READ = "projects:read"
    PROJECTS_CREATE = "projects:create"
    PROJECTS_UPDATE = "projects:update"
    PROJECTS_DELETE = "projects:delete"

    # Filaments
    FILAMENTS_READ = "filaments:read"
    FILAMENTS_CREATE = "filaments:create"
    FILAMENTS_UPDATE = "filaments:update"
    FILAMENTS_DELETE = "filaments:delete"

    # Inventory (Spool Inventory, Spool Catalog, Color Catalog)
    INVENTORY_READ = "inventory:read"
    INVENTORY_CREATE = "inventory:create"
    INVENTORY_UPDATE = "inventory:update"
    INVENTORY_DELETE = "inventory:delete"

    # Smart Plugs
    SMART_PLUGS_READ = "smart_plugs:read"
    SMART_PLUGS_CREATE = "smart_plugs:create"
    SMART_PLUGS_UPDATE = "smart_plugs:update"
    SMART_PLUGS_DELETE = "smart_plugs:delete"
    SMART_PLUGS_CONTROL = "smart_plugs:control"  # Turn on/off

    # Camera
    CAMERA_VIEW = "camera:view"

    # Maintenance
    MAINTENANCE_READ = "maintenance:read"
    MAINTENANCE_CREATE = "maintenance:create"
    MAINTENANCE_UPDATE = "maintenance:update"
    MAINTENANCE_DELETE = "maintenance:delete"

    # K-Profiles
    KPROFILES_READ = "kprofiles:read"
    KPROFILES_CREATE = "kprofiles:create"
    KPROFILES_UPDATE = "kprofiles:update"
    KPROFILES_DELETE = "kprofiles:delete"

    # Notifications
    NOTIFICATIONS_READ = "notifications:read"
    NOTIFICATIONS_CREATE = "notifications:create"
    NOTIFICATIONS_UPDATE = "notifications:update"
    NOTIFICATIONS_DELETE = "notifications:delete"

    # Notification Templates
    NOTIFICATION_TEMPLATES_READ = "notification_templates:read"
    NOTIFICATION_TEMPLATES_UPDATE = "notification_templates:update"

    # External Links
    EXTERNAL_LINKS_READ = "external_links:read"
    EXTERNAL_LINKS_CREATE = "external_links:create"
    EXTERNAL_LINKS_UPDATE = "external_links:update"
    EXTERNAL_LINKS_DELETE = "external_links:delete"

    # Discovery (network scanning)
    DISCOVERY_SCAN = "discovery:scan"

    # Firmware
    FIRMWARE_READ = "firmware:read"
    FIRMWARE_UPDATE = "firmware:update"

    # AMS History
    AMS_HISTORY_READ = "ams_history:read"

    # Stats/Metrics
    STATS_READ = "stats:read"

    # System Info
    SYSTEM_READ = "system:read"

    # Settings (admin-level)
    SETTINGS_READ = "settings:read"
    SETTINGS_UPDATE = "settings:update"
    SETTINGS_BACKUP = "settings:backup"
    SETTINGS_RESTORE = "settings:restore"

    # GitHub Backup (admin-level)
    GITHUB_BACKUP = "github:backup"
    GITHUB_RESTORE = "github:restore"

    # Cloud Auth (admin-level)
    CLOUD_AUTH = "cloud:auth"

    # API Keys (admin-level)
    API_KEYS_READ = "api_keys:read"
    API_KEYS_CREATE = "api_keys:create"
    API_KEYS_UPDATE = "api_keys:update"
    API_KEYS_DELETE = "api_keys:delete"

    # Users (admin-level)
    USERS_READ = "users:read"
    USERS_CREATE = "users:create"
    USERS_UPDATE = "users:update"
    USERS_DELETE = "users:delete"

    # Groups (admin-level)
    GROUPS_READ = "groups:read"
    GROUPS_CREATE = "groups:create"
    GROUPS_UPDATE = "groups:update"
    GROUPS_DELETE = "groups:delete"

    # WebSocket connection
    WEBSOCKET_CONNECT = "websocket:connect"


# Permission categories for UI organization
PERMISSION_CATEGORIES = {
    "Printers": [
        Permission.PRINTERS_READ,
        Permission.PRINTERS_CREATE,
        Permission.PRINTERS_UPDATE,
        Permission.PRINTERS_DELETE,
        Permission.PRINTERS_CONTROL,
        Permission.PRINTERS_FILES,
        Permission.PRINTERS_AMS_RFID,
        Permission.PRINTERS_CLEAR_PLATE,
    ],
    "Archives": [
        Permission.ARCHIVES_READ,
        Permission.ARCHIVES_CREATE,
        Permission.ARCHIVES_UPDATE_OWN,
        Permission.ARCHIVES_UPDATE_ALL,
        Permission.ARCHIVES_DELETE_OWN,
        Permission.ARCHIVES_DELETE_ALL,
        Permission.ARCHIVES_REPRINT_OWN,
        Permission.ARCHIVES_REPRINT_ALL,
    ],
    "Queue": [
        Permission.QUEUE_READ,
        Permission.QUEUE_CREATE,
        Permission.QUEUE_UPDATE_OWN,
        Permission.QUEUE_UPDATE_ALL,
        Permission.QUEUE_DELETE_OWN,
        Permission.QUEUE_DELETE_ALL,
        Permission.QUEUE_REORDER,
    ],
    "Library": [
        Permission.LIBRARY_READ,
        Permission.LIBRARY_UPLOAD,
        Permission.LIBRARY_UPDATE_OWN,
        Permission.LIBRARY_UPDATE_ALL,
        Permission.LIBRARY_DELETE_OWN,
        Permission.LIBRARY_DELETE_ALL,
    ],
    "Projects": [
        Permission.PROJECTS_READ,
        Permission.PROJECTS_CREATE,
        Permission.PROJECTS_UPDATE,
        Permission.PROJECTS_DELETE,
    ],
    "Filaments": [
        Permission.FILAMENTS_READ,
        Permission.FILAMENTS_CREATE,
        Permission.FILAMENTS_UPDATE,
        Permission.FILAMENTS_DELETE,
    ],
    "Inventory": [
        Permission.INVENTORY_READ,
        Permission.INVENTORY_CREATE,
        Permission.INVENTORY_UPDATE,
        Permission.INVENTORY_DELETE,
    ],
    "Smart Plugs": [
        Permission.SMART_PLUGS_READ,
        Permission.SMART_PLUGS_CREATE,
        Permission.SMART_PLUGS_UPDATE,
        Permission.SMART_PLUGS_DELETE,
        Permission.SMART_PLUGS_CONTROL,
    ],
    "Camera": [
        Permission.CAMERA_VIEW,
    ],
    "Maintenance": [
        Permission.MAINTENANCE_READ,
        Permission.MAINTENANCE_CREATE,
        Permission.MAINTENANCE_UPDATE,
        Permission.MAINTENANCE_DELETE,
    ],
    "K-Profiles": [
        Permission.KPROFILES_READ,
        Permission.KPROFILES_CREATE,
        Permission.KPROFILES_UPDATE,
        Permission.KPROFILES_DELETE,
    ],
    "Notifications": [
        Permission.NOTIFICATIONS_READ,
        Permission.NOTIFICATIONS_CREATE,
        Permission.NOTIFICATIONS_UPDATE,
        Permission.NOTIFICATIONS_DELETE,
        Permission.NOTIFICATION_TEMPLATES_READ,
        Permission.NOTIFICATION_TEMPLATES_UPDATE,
    ],
    "External Links": [
        Permission.EXTERNAL_LINKS_READ,
        Permission.EXTERNAL_LINKS_CREATE,
        Permission.EXTERNAL_LINKS_UPDATE,
        Permission.EXTERNAL_LINKS_DELETE,
    ],
    "Discovery": [
        Permission.DISCOVERY_SCAN,
    ],
    "Firmware": [
        Permission.FIRMWARE_READ,
        Permission.FIRMWARE_UPDATE,
    ],
    "Stats & History": [
        Permission.AMS_HISTORY_READ,
        Permission.STATS_READ,
    ],
    "System": [
        Permission.SYSTEM_READ,
    ],
    "Settings": [
        Permission.SETTINGS_READ,
        Permission.SETTINGS_UPDATE,
        Permission.SETTINGS_BACKUP,
        Permission.SETTINGS_RESTORE,
    ],
    "Backup": [
        Permission.GITHUB_BACKUP,
        Permission.GITHUB_RESTORE,
    ],
    "Cloud": [
        Permission.CLOUD_AUTH,
    ],
    "API Keys": [
        Permission.API_KEYS_READ,
        Permission.API_KEYS_CREATE,
        Permission.API_KEYS_UPDATE,
        Permission.API_KEYS_DELETE,
    ],
    "User Management": [
        Permission.USERS_READ,
        Permission.USERS_CREATE,
        Permission.USERS_UPDATE,
        Permission.USERS_DELETE,
        Permission.GROUPS_READ,
        Permission.GROUPS_CREATE,
        Permission.GROUPS_UPDATE,
        Permission.GROUPS_DELETE,
    ],
    "WebSocket": [
        Permission.WEBSOCKET_CONNECT,
    ],
}


# All permissions as a list
ALL_PERMISSIONS = [p.value for p in Permission]


# Default group definitions
DEFAULT_GROUPS = {
    "Administrators": {
        "description": "Full access to all features and settings",
        "permissions": ALL_PERMISSIONS,  # All permissions
        "is_system": True,
    },
    "Operators": {
        "description": "Can control printers, manage queue and archives, view settings",
        "permissions": [
            # Printers - full control
            Permission.PRINTERS_READ.value,
            Permission.PRINTERS_CREATE.value,
            Permission.PRINTERS_UPDATE.value,
            Permission.PRINTERS_DELETE.value,
            Permission.PRINTERS_CONTROL.value,
            Permission.PRINTERS_FILES.value,
            Permission.PRINTERS_AMS_RFID.value,
            Permission.PRINTERS_CLEAR_PLATE.value,
            # Archives - own items only
            Permission.ARCHIVES_READ.value,
            Permission.ARCHIVES_CREATE.value,
            Permission.ARCHIVES_UPDATE_OWN.value,
            Permission.ARCHIVES_DELETE_OWN.value,
            Permission.ARCHIVES_REPRINT_OWN.value,
            # Queue - own items only
            Permission.QUEUE_READ.value,
            Permission.QUEUE_CREATE.value,
            Permission.QUEUE_UPDATE_OWN.value,
            Permission.QUEUE_DELETE_OWN.value,
            Permission.QUEUE_REORDER.value,
            # Library - own items only
            Permission.LIBRARY_READ.value,
            Permission.LIBRARY_UPLOAD.value,
            Permission.LIBRARY_UPDATE_OWN.value,
            Permission.LIBRARY_DELETE_OWN.value,
            # Projects - full access
            Permission.PROJECTS_READ.value,
            Permission.PROJECTS_CREATE.value,
            Permission.PROJECTS_UPDATE.value,
            Permission.PROJECTS_DELETE.value,
            # Filaments - full access
            Permission.FILAMENTS_READ.value,
            Permission.FILAMENTS_CREATE.value,
            Permission.FILAMENTS_UPDATE.value,
            Permission.FILAMENTS_DELETE.value,
            # Inventory - full access
            Permission.INVENTORY_READ.value,
            Permission.INVENTORY_CREATE.value,
            Permission.INVENTORY_UPDATE.value,
            Permission.INVENTORY_DELETE.value,
            # Smart Plugs - full access
            Permission.SMART_PLUGS_READ.value,
            Permission.SMART_PLUGS_CREATE.value,
            Permission.SMART_PLUGS_UPDATE.value,
            Permission.SMART_PLUGS_DELETE.value,
            Permission.SMART_PLUGS_CONTROL.value,
            # Camera - view
            Permission.CAMERA_VIEW.value,
            # Maintenance - full access
            Permission.MAINTENANCE_READ.value,
            Permission.MAINTENANCE_CREATE.value,
            Permission.MAINTENANCE_UPDATE.value,
            Permission.MAINTENANCE_DELETE.value,
            # K-Profiles - full access
            Permission.KPROFILES_READ.value,
            Permission.KPROFILES_CREATE.value,
            Permission.KPROFILES_UPDATE.value,
            Permission.KPROFILES_DELETE.value,
            # Notifications - full access
            Permission.NOTIFICATIONS_READ.value,
            Permission.NOTIFICATIONS_CREATE.value,
            Permission.NOTIFICATIONS_UPDATE.value,
            Permission.NOTIFICATIONS_DELETE.value,
            Permission.NOTIFICATION_TEMPLATES_READ.value,
            Permission.NOTIFICATION_TEMPLATES_UPDATE.value,
            # External Links - full access
            Permission.EXTERNAL_LINKS_READ.value,
            Permission.EXTERNAL_LINKS_CREATE.value,
            Permission.EXTERNAL_LINKS_UPDATE.value,
            Permission.EXTERNAL_LINKS_DELETE.value,
            # Discovery
            Permission.DISCOVERY_SCAN.value,
            # Firmware - read only
            Permission.FIRMWARE_READ.value,
            # Stats & History
            Permission.AMS_HISTORY_READ.value,
            Permission.STATS_READ.value,
            Permission.SYSTEM_READ.value,
            # Settings - read only
            Permission.SETTINGS_READ.value,
            # WebSocket
            Permission.WEBSOCKET_CONNECT.value,
        ],
        "is_system": True,
    },
    "Viewers": {
        "description": "Read-only access to printers, archives, and queue",
        "permissions": [
            # Read-only access
            Permission.PRINTERS_READ.value,
            Permission.ARCHIVES_READ.value,
            Permission.QUEUE_READ.value,
            Permission.LIBRARY_READ.value,
            Permission.PROJECTS_READ.value,
            Permission.FILAMENTS_READ.value,
            Permission.INVENTORY_READ.value,
            Permission.SMART_PLUGS_READ.value,
            Permission.CAMERA_VIEW.value,
            Permission.MAINTENANCE_READ.value,
            Permission.KPROFILES_READ.value,
            Permission.NOTIFICATIONS_READ.value,
            Permission.NOTIFICATION_TEMPLATES_READ.value,
            Permission.EXTERNAL_LINKS_READ.value,
            Permission.FIRMWARE_READ.value,
            Permission.AMS_HISTORY_READ.value,
            Permission.STATS_READ.value,
            Permission.SYSTEM_READ.value,
            Permission.SETTINGS_READ.value,
            Permission.WEBSOCKET_CONNECT.value,
        ],
        "is_system": True,
    },
}
