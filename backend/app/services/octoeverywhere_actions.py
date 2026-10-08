"""OctoEverywhere actions using Bambuddy's existing AI failure notification event."""

from backend.app.services.obico_actions import execute_action as execute_detection_action


async def execute_action(printer_id: int, action: str, task_name: str, print_quality: int) -> None:
    """Dispatch a suggested action without treating print quality as confidence."""
    await execute_detection_action(printer_id, action, task_name, None, print_quality=print_quality)
