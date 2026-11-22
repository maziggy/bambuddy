import type { NotificationPhotoEvent, NotificationProvider } from '../api/client';

type EventToggle =
  | 'on_print_start'
  | 'on_print_complete'
  | 'on_print_failed'
  | 'on_print_stopped'
  | 'on_print_progress'
  | 'on_first_layer_complete'
  | 'on_printer_error'
  | 'on_ai_failure_detection'
  | 'on_plate_not_empty'
  | 'on_print_confirm_request';

/**
 * The events that can carry a camera snapshot, with the provider toggle that
 * switches the event on and the label it already has in the event list.
 * A provider can send any of them without the photo (#2877).
 */
export const PHOTO_EVENTS: ReadonlyArray<{ event: NotificationPhotoEvent; toggle: EventToggle; labelKey: string }> = [
  { event: 'print_start', toggle: 'on_print_start', labelKey: 'notifications.start' },
  { event: 'print_progress', toggle: 'on_print_progress', labelKey: 'notifications.progress' },
  { event: 'first_layer_complete', toggle: 'on_first_layer_complete', labelKey: 'notifications.firstLayerCompleteLabel' },
  { event: 'print_complete', toggle: 'on_print_complete', labelKey: 'notifications.complete' },
  { event: 'print_failed', toggle: 'on_print_failed', labelKey: 'notifications.failed' },
  { event: 'print_stopped', toggle: 'on_print_stopped', labelKey: 'notifications.stopped' },
  { event: 'print_confirm_request', toggle: 'on_print_confirm_request', labelKey: 'notifications.printConfirmRequest' },
  { event: 'printer_error', toggle: 'on_printer_error', labelKey: 'notifications.error' },
  { event: 'ai_failure_detection', toggle: 'on_ai_failure_detection', labelKey: 'notifications.aiFailureDetection' },
  { event: 'plate_not_empty', toggle: 'on_plate_not_empty', labelKey: 'notifications.plateNotEmpty' },
];

/** The photo events whose notification the provider has switched on. */
export function enabledPhotoEvents(toggles: Partial<Pick<NotificationProvider, EventToggle>>) {
  return PHOTO_EVENTS.filter(({ toggle }) => toggles[toggle]);
}
