# OctoEverywhere AI Failure Detection

Bambuddy can monitor running 3D prints with [OctoEverywhere's cloud AI failure detection.](https://docs.octoeverywhere.com/ai-failure-detection-apis/overview/) After you add the OctoEverywhere API key, the system will automatically monitor all your 3D printers (unless you configure it otherwise). It can send notifications or automatically pause the print when it detects a failure.

## Privacy
Camera snapshots are sent to OctoEverywhere for analysis and deleted immediately after analysis completes. OctoEverywhere does not store snapshots, nor does it use them for AI training. See the OctoEverywhere [privacy policy for details.](https://octoeverywhere.com/privacy#gadget-developer-api)

## Setup

1. [Set up an OctoEverywhere Gadget API key](https://octoeverywhere.com/gadgetapi) in your OctoEverywhere account.
2. Open **Settings → Failure Detection** and choose **OctoEverywhere** under **Provider**.
3. Turn on **OctoEverywhere AI Detection**, paste the key into **Gadget API key**, and click **Test**.
4. Confirm **OctoEverywhere Gadget API key verification successful!** appears. Monitoring starts automatically for connected printers while they are printing.
5. To receive alerts, configure a notification provider under **Settings → Notifications** and enable its **AI Failure Detection** event. Check that its printer filters include the printers you want to monitor.

If notifications are missing or do not cover every monitored printer, the settings page shows an alert with a **Configure notifications** link for users with notification access. This checks the configured event subscription and printer filters; use the notification provider's own test to check delivery.

## Detection settings

| Setting | Behavior |
|---------|----------|
| **Confidence** | How sure OctoEverywhere must be before it suggests a warning or pause. Choose **Lowest**, **Low**, **Medium** (the default), **High**, or **Highest**. Lower confidence reports possible failures sooner but may have more false positives; higher confidence takes longer to report but is more certain before notifying you or pausing the print. |
| **Action on detected failure** | **Notify only** is the default. **Pause print** sends a pause command when OctoEverywhere suggests pausing. **Pause and cut power** also turns off enabled smart plugs linked to that printer. |
| **Monitored Printers** | **Monitor all connected printers** is selected by default. Clear it to choose individual printers. |
| **Inspection interval** | **20 seconds** by default. Choose a fixed interval from **5 to 30 seconds** with the slider. |

Warnings send at most one notification per monitored print session. If OctoEverywhere later suggests pausing, **Pause print** or **Pause and cut power** runs once and sends its own notification, so you know the printer stopped. With **Notify only**, no second notification is sent and the printer continues printing after a warning or failure suggestion.

## Status and print quality

The **Status** and **Recent Detections** cards show monitored prints and their latest results. **Monitoring** distinguishes disabled detection, a missing key, an unavailable service, waiting for the first result, available results, and errors that need attention. A running service by itself does not confirm that a print has been checked. The printer card's AI badge opens the detection details.

| Badge | Meaning |
|-------|---------|
| **Starting** | The first usable result has not arrived yet. |
| **Safe** | The latest successful check did not suggest a warning or pause. |
| **Warning** | OctoEverywhere suggested warning about a possible issue. |
| **Failure** | OctoEverywhere suggested pausing the print. |
| **Not checking** | A camera, connection, or API problem prevented a result. Open the details or Status card for the reason. |

**Print quality** ranges from **1/10 to 10/10**, with higher values indicating better quality. It is not a failure probability. Bambuddy bases warnings and actions on OctoEverywhere's warning/pause suggestions, so quality alone does not trigger an action. [Process API](https://docs.octoeverywhere.com/ai-failure-detection-apis/process/#printquality)

## Troubleshooting

- **Key rejected:** Check that the saved OctoEverywhere Gadget API key is correct, then click **Test**.
- **Key disabled:** [Contact OctoEverywhere support](https://octoeverywhere.com/support) to restore access, then click **Test**.
- **Usage limit reached:** When the API returns `OE_FREE_USAGE_LIMIT_REACHED`, Bambuddy shows “Usage limit reached.” with a [Set up billing to continue](https://octoeverywhere.com/gadgetapi) link. Wait for the next monthly allowance, or optionally set up billing and turn off **Free Usage Only** on the account page. If billing is already configured, only the **Free Usage Only** setting needs changing. After the allowance renews or the account settings are updated, click **Test** to resume inspections.
- **IP restricted:** Use the original Gadget API key associated with the public IP address, or [contact OctoEverywhere support](https://octoeverywhere.com/support) if it is unavailable or the IP is shared with another account. Changing billing settings or creating another key does not remove the restriction. Click **Test** after resolving access.
- **Account access problem:** Check the OctoEverywhere API account status and resolve the reported issue, then click **Test**.
- **Camera capture failed:** Verify that the printer's camera works in Bambuddy and that the printer is connected. Detection uses the configured built-in or external camera.
- **Connection failure:** Check Bambuddy's internet access, DNS, and outbound HTTPS access to OctoEverywhere. Temporary service failures retry automatically; repeated failures delay subsequent retries.
- **Checks slower than the selected interval:** Camera capture, processing time, the server's minimum interval, rate limits, and error retry delays can lengthen the interval. The selected interval never overrides the server's minimum or retry timing.
- **False alarms or late alerts:** Raise **Confidence** if healthy prints trigger warnings; lower it to report possible failures sooner.
- **No active prints:** Check the enable toggle, monitored-printer selection, printer connection, and that the printer is actively printing.
- **No notification:** Enable the **AI Failure Detection** event on a working notification provider and check its printer filters. Detection does not enable notification subscriptions automatically.

Key, account, IP restriction, and usage-limit errors stop inspections across all monitored printers until access is restored and **Test** succeeds with the configured key, or a replacement key is saved. These errors do not pause the printers. **Test** verifies access by creating a context; it does not upload an image or verify the remaining inspection allowance. A successful test resumes inspections, but the next inspection can stop monitoring again if the allowance is still exhausted. Creating another context or switching processing URLs does not reset the allowance.

A successful **Test** with the same key preserves existing monitoring contexts. Saving a replacement key creates new contexts while preserving notification/action tracking and the current inspection timing.

The upstream [error-handling reference](https://docs.octoeverywhere.com/ai-failure-detection-apis/developer-docs/overview/#error-handling) explains API errors.

### Inspection debug logs

Each inspection logs its start and whether it reuses a context. Successful results include the printer ID, frame count, verdict, print quality, warning/pause suggestions, faster-inspection flag, server minimum, and effective interval until the next check. Failed attempts log a safe error message, recognized error code, and retry delay when another attempt is scheduled; canceled or discarded inspections are also recorded. Scheduled polls that are not yet due do not produce inspection logs. These diagnostics omit API keys, context IDs/URLs, image data, and raw API response bodies.

## Bambuddy API reference

The settings API uses these keys:

| Key | Default / values |
|-----|------------------|
| `octoeverywhere_enabled` | `false` |
| `octoeverywhere_api_key` | Write-only Gadget API key. Omit it from a settings update to preserve the saved key; send an empty string to clear it. Always empty in settings responses. |
| `octoeverywhere_api_key_configured` | Read-only boolean indicating whether a key is saved; defaults to `false` |
| `octoeverywhere_poll_interval` | `20` by default; an integer from `5` to `30` seconds; normally clamped to the server minimum, temporarily shortened to its recommended interval when `FasterInspectionSuggested` is true |
| `octoeverywhere_confidence` | `medium`; accepts `lowest`, `low`, `medium`, `high`, `highest`, sent as API confidence levels 1, 2, 3, 4, 5 respectively for both `WarningConfidenceLevel` and `PauseConfidenceLevel` |
| `octoeverywhere_action` | `notify`; accepts `notify`, `pause`, `pause_and_off` |
| `octoeverywhere_enabled_printers` | Empty string for all printers, or a JSON-encoded array of printer IDs; `[]` monitors none |

| Endpoint | Purpose | Permission when authentication is enabled |
|----------|---------|-------------------------------------------|
| `GET /api/v1/octoeverywhere/status` | Service status, `last_error_code`, `api_key_configured`, `poll_interval`, per-printer quality, recent history, and notification coverage | `settings:read` |
| `GET /api/v1/octoeverywhere/printer-status` | Printer-card monitoring state; error messages and codes require `settings:read` | `printers:read` |
| `POST /api/v1/octoeverywhere/test-connection` | Test key/account eligibility using optional `api_key` and `confidence` fields, falling back to saved settings when omitted; a successful test with the configured key resumes blocked inspections, but does not verify the remaining allowance; does not update settings or upload an image | `settings:update` |

Notification providers use the existing `on_ai_failure_detection` event subscription. The status response's `notifications.configured` indicates that an enabled provider subscribes to this event, and `notifications.uncovered_printers` lists monitored active printer IDs without a matching subscription.

Each printer status and Test result includes a nullable `error_code`. Status responses also include `last_error_code` alongside `last_error`. These codes identify known API errors without exposing remote response details.
