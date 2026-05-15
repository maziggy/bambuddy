# Telegram Command Bot

Bambuddy can use an existing Telegram notification provider as a small read-only command bot.
The bot is useful when you want to request printer status, camera snapshots, or queue information from a Telegram chat without opening the web UI.

## Requirements

- A Telegram notification provider in Bambuddy
- A valid bot token from `@BotFather`
- The Telegram `chat_id` that is allowed to control the bot
- Network access from Bambuddy to `api.telegram.org`

Only messages from the configured `chat_id` are accepted. Messages from other chats are rejected.

## Enable Commands

1. Open Bambuddy.
2. Go to `Settings` -> `Notifications`.
3. Create or edit a Telegram provider.
4. Enter `Bot Token` and `Chat ID`.
5. Enable `Telegram chat commands`.
6. Save the provider.

Existing Telegram providers have commands enabled by default unless `bot_commands_enabled` is set to `false` in the provider config.

Printer control commands are separate and disabled by default. Enable `Telegram control commands` only if the configured chat should be allowed to pause, resume, stop, release queued jobs, switch the chamber light, or acknowledge a cleared build plate.

## Commands

### `/help`

Shows the command list.

### `/printers`

Lists active printers known to Bambuddy.

### `/dashboard`

Shows a compact overview of all active printers:

- Online/offline count
- Running and paused count
- Warning count
- Current printer state
- Progress and remaining time for active prints

### `/status`

Shows the status of all active printers, including connection state, current print, progress, remaining time, layer information, and temperatures when available.

### `/status <printer>`

Shows status for one printer.

Examples:

```text
/status rocketman
/status a1
/status mini
```

Printer matching accepts printer ID, exact name, model, or a partial name.

### `/eta`

Shows remaining times for all currently running or paused prints.

### `/eta <printer>`

Shows the remaining time for one printer.

### `/errors`

Shows current HMS warnings and errors across all printers.

The output includes:

- Severity
- Module
- Short HMS code
- Raw code
- Attribute value
- Human-readable description when Bambuddy knows the code

### `/errors <printer>`

Shows current HMS warnings and errors for one printer.

### `/ams <printer>`

Shows AMS and tray information for one printer, including humidity, temperature, drying time, tray names, colors, and remaining percentages when available.

### `/filament [grams]`

Lists active Bambuddy inventory spools below the given remaining weight.

Examples:

```text
/filament
/filament 150
```

Without a value, the command uses `200 g`.

### `/history [count]`

Shows recent print log entries.

Examples:

```text
/history
/history 10
```

The command returns between 1 and 15 entries.

### `/maintenance`

Shows active maintenance items sorted by due date.

### `/maintenance <printer>`

Shows active maintenance items for one printer.

### `/photo <printer>`

Captures and sends a current camera snapshot for one printer.

Examples:

```text
/photo rocketman
/photo a1
/photo mini
```

Bambuddy uses the configured external camera first. If none is configured, it captures from the printer camera using the same camera capture service used by Bambuddy snapshots.

### `/queue`

Shows queue counts and the next active jobs grouped by printer or target model.

The output includes:

- Queue item ID
- Queue position
- File or print name
- Target printer or target model
- Job status
- Manual-start marker
- Waiting reason when present

## Optional Control Commands

Control commands are disabled by default and require `Telegram control commands` to be enabled on the Telegram notification provider.

### `/pause <printer>`

Sends a pause command to the printer.

### `/resume <printer>`

Sends a resume command to the printer.

### `/stop <printer> confirm`

Stops/cancels the active print. The `confirm` suffix is required intentionally.

### `/light <printer> on`

Turns the chamber light on.

### `/light <printer> off`

Turns the chamber light off.

### `/clearplate <printer>`

Marks the build plate as cleared in Bambuddy so the queue can continue.

### `/startqueue <queue-id>`

Clears the manual-start hold for a pending queue item so Bambuddy's queue worker can pick it up.

## Security Notes

- By default, the command bot exposes read-only commands only.
- Read-only commands and control commands can be enabled separately.
- Control commands are disabled by default.
- `/stop` requires an explicit `confirm` suffix.
- Only the configured `chat_id` is accepted.
- If the bot is used in a group chat, every group member who can write in that group can use the enabled commands.
- Keep the bot token private.

## Performance Notes

- The bot uses Telegram long polling, so it waits for new messages instead of running a tight loop.
- If no enabled Telegram provider exists, it sleeps for 30 seconds before checking again.
- Expensive output is capped: queue, history, filament, and maintenance commands return limited result sets.
- Camera snapshots are only captured when a user sends `/photo <printer>`.
- The bot does not scan camera streams, print files, or inventory in the background.

## Troubleshooting

### No Reply

Check that:

- The Telegram provider is enabled.
- `Telegram chat commands` is enabled.
- `bot_token` is correct.
- `chat_id` matches the chat where commands are sent.
- Bambuddy can reach `https://api.telegram.org`.

### Photos Fail

Check that the camera works in Bambuddy first. If the printer camera is disabled or unreachable, `/photo <printer>` cannot send an image.

### Old Messages Are Not Answered After Startup

This is intentional. Bambuddy marks existing Telegram backlog as consumed when the bot starts, so old commands do not trigger stale replies after a restart.
