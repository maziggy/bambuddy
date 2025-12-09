# Notification Templates Management System

## Overview

Replace hardcoded notification messages with a flexible template system that allows users to customize notification content per event type, with provider-specific formatting support.

---

## Data Model

### New Table: `notification_templates`

```sql
CREATE TABLE notification_templates (
    id INTEGER PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,  -- print_start, print_complete, etc.
    name VARCHAR(100) NOT NULL,       -- User-friendly name
    title_template TEXT NOT NULL,     -- Template for notification title
    body_template TEXT NOT NULL,      -- Template for notification body
    is_default BOOLEAN DEFAULT 0,     -- System default (non-deletable)
    created_at DATETIME,
    updated_at DATETIME
);
```

**Event Types:**
- `print_start`
- `print_complete`
- `print_failed`
- `print_stopped`
- `print_progress`
- `printer_offline`
- `printer_error`
- `filament_low`
- `maintenance_due`
- `test` (for test notifications)

---

## Template Variables

Variables use `{variable_name}` syntax (Python format strings).

### Per-Event Variables:

| Event | Variables |
|-------|-----------|
| `print_start` | `{printer}`, `{filename}`, `{estimated_time}` |
| `print_complete` | `{printer}`, `{filename}`, `{duration}`, `{filament_grams}` |
| `print_failed` | `{printer}`, `{filename}`, `{duration}`, `{reason}` |
| `print_stopped` | `{printer}`, `{filename}`, `{duration}` |
| `print_progress` | `{printer}`, `{filename}`, `{progress}`, `{remaining_time}` |
| `printer_offline` | `{printer}` |
| `printer_error` | `{printer}`, `{error_type}`, `{error_detail}` |
| `filament_low` | `{printer}`, `{slot}`, `{remaining_percent}`, `{color}` |
| `maintenance_due` | `{printer}`, `{items}` (formatted list) |
| `test` | `{app_name}` |

### Common Variables (all events):
- `{timestamp}` - Current date/time
- `{app_name}` - "Bambuddy"

---

## Default Templates

Pre-seeded templates for each event (marked `is_default=True`):

```
print_start:
  title: "Print Started"
  body: "{printer}: {filename}\nEstimated: {estimated_time}"

print_complete:
  title: "Print Completed"
  body: "{printer}: {filename}\nTime: {duration}\nFilament: {filament_grams}g"

print_failed:
  title: "Print Failed"
  body: "{printer}: {filename}\nTime: {duration}\nReason: {reason}"

print_stopped:
  title: "Print Stopped"
  body: "{printer}: {filename}\nTime: {duration}"

print_progress:
  title: "Print {progress}% Complete"
  body: "{printer}: {filename}\nRemaining: {remaining_time}"

printer_offline:
  title: "Printer Offline"
  body: "{printer} has disconnected"

printer_error:
  title: "Printer Error: {error_type}"
  body: "{printer}\n{error_detail}"

filament_low:
  title: "Filament Low"
  body: "{printer}: Slot {slot} at {remaining_percent}%"

maintenance_due:
  title: "Maintenance Due"
  body: "{printer}:\n{items}"

test:
  title: "Bambuddy Test"
  body: "This is a test notification. If you see this, notifications are working!"
```

---

## Provider-Specific Formatting

The template system supports provider-specific formatting via a simple approach:

1. **Plain text** (default) - Used for CallMeBot, ntfy, Pushover, Email
2. **Markdown** - Automatically applied for Telegram (wrap title in `*bold*`)

The notification service will:
- Render the template with variables
- Apply provider-specific formatting when sending

---

## Implementation Steps

### Backend

1. **Create model** `backend/app/models/notification_template.py`
   - NotificationTemplate SQLAlchemy model

2. **Create schemas** `backend/app/schemas/notification_template.py`
   - NotificationTemplateCreate, Update, Response
   - TemplateVariables (documentation of available vars per event)

3. **Add migration** in `backend/app/core/database.py`
   - Create table if not exists
   - Seed default templates

4. **Create API routes** `backend/app/api/routes/notification_templates.py`
   - `GET /api/v1/notification-templates` - List all templates
   - `GET /api/v1/notification-templates/{id}` - Get single template
   - `PUT /api/v1/notification-templates/{id}` - Update template
   - `POST /api/v1/notification-templates/{id}/reset` - Reset to default
   - `GET /api/v1/notification-templates/variables` - List available variables per event
   - `POST /api/v1/notification-templates/preview` - Preview template with sample data

5. **Update notification service** `backend/app/services/notification_service.py`
   - Load templates from database
   - Render templates with variables
   - Remove hardcoded message builders

6. **Register routes** in `backend/app/main.py`

### Frontend

7. **Add API client methods** `frontend/src/api/client.ts`
   - getNotificationTemplates, updateNotificationTemplate, etc.

8. **Create template editor component** `frontend/src/components/NotificationTemplateEditor.tsx`
   - Template editing UI with variable insertion buttons
   - Live preview with sample data
   - Reset to default button

9. **Update SettingsPage** `frontend/src/pages/SettingsPage.tsx`
   - Add "Templates" sub-section in Notifications tab
   - List all templates with edit capability

---

## UI Design

### Templates Section (in Settings > Notifications)

```
+--------------------------------------------------+
| Message Templates                                |
| Customize notification messages for each event   |
+--------------------------------------------------+
|                                                  |
| +----------------+  +----------------+           |
| | Print Started  |  | Print Complete |  ...     |
| | "Print Started"|  | "Print Compl..." |        |
| | [Edit]         |  | [Edit]         |          |
| +----------------+  +----------------+           |
|                                                  |
+--------------------------------------------------+
```

### Template Editor Modal

```
+--------------------------------------------------+
| Edit Template: Print Complete              [X]   |
+--------------------------------------------------+
| Title:                                           |
| [Print Completed_________________________]       |
|                                                  |
| Body:                                            |
| +----------------------------------------------+ |
| | {printer}: {filename}                        | |
| | Time: {duration}                             | |
| | Filament: {filament_grams}g                  | |
| +----------------------------------------------+ |
|                                                  |
| Available Variables:                             |
| [+printer] [+filename] [+duration] [+filament]   |
|                                                  |
| Preview:                                         |
| +----------------------------------------------+ |
| | Title: Print Completed                       | |
| | Body:  Bambu X1C: Benchy.3mf                 | |
| |        Time: 1h 23m                          | |
| |        Filament: 15.2g                       | |
| +----------------------------------------------+ |
|                                                  |
| [Reset to Default]              [Cancel] [Save]  |
+--------------------------------------------------+
```

---

## File Changes Summary

| File | Action |
|------|--------|
| `backend/app/models/notification_template.py` | Create |
| `backend/app/schemas/notification_template.py` | Create |
| `backend/app/api/routes/notification_templates.py` | Create |
| `backend/app/core/database.py` | Modify (add migration + seeding) |
| `backend/app/models/__init__.py` | Modify (export new model) |
| `backend/app/services/notification_service.py` | Modify (use templates) |
| `backend/app/main.py` | Modify (register routes) |
| `frontend/src/api/client.ts` | Modify (add API methods + types) |
| `frontend/src/components/NotificationTemplateEditor.tsx` | Create |
| `frontend/src/pages/SettingsPage.tsx` | Modify (add templates section) |

---

## Notes

- Default templates cannot be deleted, only modified and reset
- Templates are language-agnostic (user writes in their preferred language)
- The existing `notification_language` setting can be removed later (templates replace i18n)
- Variables that are unavailable for an event will render as empty string
- Template rendering uses safe formatting (missing vars don't crash)
