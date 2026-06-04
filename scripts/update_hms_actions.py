import asyncio
import json

import requests

HMS_ACTIONS_JSON_PATH = "backend/app/data/hms_actions.json"
HMS_REQUEST_URL = "https://e.bambulab.com/hms/GetActionImage.php"

HMS_ID_TO_ACTION_NAME_MAP: dict[int, str] = {
    2: "RESUME_PRINTING",
    3: "RESUME_PRINTING_DEFECTS",
    4: "RESUME_PRINTING_PROBELM_SOLVED",
    5: "STOP_PRINTING",
    6: "CHECK_ASSISTANT",
    7: "FILAMENT_EXTRUDED",
    8: "RETRY_FILAMENT_EXTRUDED",
    9: "CONTINUE",
    10: "LOAD_VIRTUAL_TRAY",
    11: "OK_BUTTON",
    12: "FILAMENT_LOAD_RESUME",
    13: "JUMP_TO_LIVEVIEW",
    23: "NO_REMINDER_NEXT_TIME",
    24: "REFRESH_NOZZLE",
    25: "IGNORE_NO_REMINDER_NEXT_TIME",
    27: "IGNORE_RESUME",
    28: "PROBLEM_SOLVED_RESUME",
    29: "TURN_OFF_FIRE_ALARM",
    34: "RETRY_PROBLEM_SOLVED",
    35: "STOP_DRYING",
    37: "CANCLE",  # Note: "CANCLE" is intentionally misspelled in the BambuStudio source code
    39: "REMOVE_CLOSE_BTN",
    41: "PROCEED",
    49: "OK_JUMP_RACK",
    51: "ABORT",
    54: "DISABLE_PURIFICATION",
    57: "DONT_REMIND_NEXT_TIME",
    10000: "DBL_CHECK_CANCEL",
    10001: "DBL_CHECK_DONE",
    10002: "DBL_CHECK_RETRY",
    10003: "DBL_CHECK_RESUME",
    10004: "DBL_CHECK_OK",
}


async def main():
    error_to_action_map: dict[str, list[str]] = {}
    # get the json response from the url
    response = requests.get(HMS_REQUEST_URL)
    if response.status_code == 200:
        data = response.json()
        ready_data = {}
        for item in data["data"]:
            # error_code = item["ecode"][:4] + "_" + item["ecode"][4:]
            mapped_actions = []
            for hms_id in item["actions"]:
                if hms_id not in HMS_ID_TO_ACTION_NAME_MAP:
                    print(f"Warning: Unrecognized HMS action ID {hms_id} for error code {item['ecode']}")
                else:
                    mapped_actions.append(HMS_ID_TO_ACTION_NAME_MAP.get(hms_id, f"UNKNOWN_ACTION_{hms_id}"))
            print(f"ecode: {item['ecode']}, actions: {mapped_actions}, device: {item['device']}")
            if item["device"] not in ready_data:
                ready_data[item["device"]] = {}
            ready_data[item["device"]][item["ecode"]] = mapped_actions
            # ready_data.append(
            #     {
            #         "ecode": item["ecode"],
            #         "actions": mapped_actions,
            #         "device": item["device"],
            #     }
            # )
        with open(HMS_ACTIONS_JSON_PATH, "w") as f:
            json.dump(ready_data, f, indent=4)
    else:
        print("Failed to fetch data")
    print(error_to_action_map)

    # autogenerate


if __name__ == "__main__":
    asyncio.run(main())
