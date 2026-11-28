"""AMS pushes recorded from an H2C while spools were pulled and reloaded (#3756).

Recorded 2026-11-27 from Bambuddy's MQTT log of an H2C (AMS 2 Pro units 0
and 1, AMS-HT unit 128, all nine slots full; AMS firmware 04.00.22.04), and
cut down to what Bambuddy's AMS handling reads. The tag ids of the one Bambu
spool are replaced with made-up ones.

``s`` is seconds from the first push. ``top`` holds the push's top-level AMS
fields; ``trays`` the trays that changed since the push before, keyed
``"<ams_id>.<tray_id>"``, as the printer sent them (an empty slot sends only
``id`` and ``state``). Every push the printer sent carries all trays;
``h2c_pushes()`` rebuilds them.

What happens:

* 122 s: the untagged PETG in AMS 1 slot 3 (``1.2``) is pulled. Bit 6
  clears at once (``100bf``) and the tray reports state 10 with no filament.
* 138.8 s: it goes back in. The bit is back at once; the tray runs through
  states 1, 17, 21 and 5 with no filament, reaches 11 at 161.6 s and gets the
  slot's last filament (``SUN22003``) at 162.9 s, 24 s after insertion.
* 180.8 s: the Bambu RFID PLA in AMS 0 slot 1 (``0.0``) is pulled (``100fe``).
* 201.2 s: it goes back in. The tag is read at 220.3 s (state 27) and the slot
  reaches state 11 at 222.7 s, 21.5 s after insertion. Meanwhile the AMS
  reads slot ``0.1`` again (27, then 11) without anything moving.
* The H2C reports ``power_on_flag: False`` throughout, although it is on.
"""

import copy

H2C_AMS_LOAD_DELTAS = [
    {
        "s": 0,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {
            "0.0": {
                "id": "0",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "C12E1FFF",
                "tray_info_idx": "GFA00",
                "tag_uid": "A1B2C3D400000100",
                "tray_uuid": "F00DFEEDF00DFEEDF00DFEEDF00DFEED",
                "remain": 62,
            },
            "0.1": {
                "id": "1",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "GFA01",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "0.2": {
                "id": "2",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "",
                "tray_color": "006AB8FF",
                "tray_info_idx": "SUN20008",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "0.3": {
                "id": "3",
                "state": 11,
                "tray_type": "PETG",
                "tray_sub_brands": "",
                "tray_color": "FFFFFFFF",
                "tray_info_idx": "P39d4202",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "1.0": {
                "id": "0",
                "state": 11,
                "tray_type": "PETG",
                "tray_sub_brands": "",
                "tray_color": "808080FF",
                "tray_info_idx": "GFG99",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "1.1": {
                "id": "1",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "",
                "tray_color": "FFFFFFFF",
                "tray_info_idx": "Pa8c5a1a",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "1.2": {
                "id": "2",
                "state": 11,
                "tray_type": "PETG",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "SUN22003",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "1.3": {
                "id": "3",
                "state": 11,
                "tray_type": "PETG",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "SUN22003",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
            "128.0": {
                "id": "0",
                "state": 11,
                "tray_type": "PETG-CF",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "GFG98",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            },
        },
    },
    {
        "s": 122,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100bf",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 10}},
    },
    {
        "s": 138.8,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 1}},
    },
    {
        "s": 140,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 17}},
    },
    {
        "s": 141.1,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 1}},
    },
    {
        "s": 142.4,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 21}},
    },
    {
        "s": 143.4,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 5}},
    },
    {
        "s": 145.8,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 21}},
    },
    {
        "s": 161.6,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"1.2": {"id": "2", "state": 11}},
    },
    {
        "s": 162.9,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {
            "1.2": {
                "id": "2",
                "state": 11,
                "tray_type": "PETG",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "SUN22003",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            }
        },
    },
    {
        "s": 180.8,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100fe",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"0.0": {"id": "0", "state": 10}},
    },
    {
        "s": 201.2,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"0.0": {"id": "0", "state": 1}},
    },
    {
        "s": 203.6,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"0.0": {"id": "0", "state": 17}},
    },
    {
        "s": 204.6,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"0.0": {"id": "0", "state": 5}},
    },
    {
        "s": 205.7,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "0",
            "insert_flag": True,
        },
        "trays": {},
    },
    {
        "s": 206.8,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "1",
            "insert_flag": True,
        },
        "trays": {
            "0.1": {
                "id": "1",
                "state": 27,
                "tray_type": "PLA",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "GFA01",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            }
        },
    },
    {
        "s": 208.1,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {
            "0.1": {
                "id": "1",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "",
                "tray_color": "000000FF",
                "tray_info_idx": "GFA01",
                "tag_uid": "0000000000000000",
                "tray_uuid": "00000000000000000000000000000000",
                "remain": -1,
            }
        },
    },
    {
        "s": 209.1,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {"0.0": {"id": "0", "state": 21}},
    },
    {
        "s": 210.4,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "0",
            "insert_flag": True,
        },
        "trays": {},
    },
    {
        "s": 220.3,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "0",
            "insert_flag": True,
        },
        "trays": {
            "0.0": {
                "id": "0",
                "state": 27,
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "C12E1FFF",
                "tray_info_idx": "GFA00",
                "tag_uid": "A1B2C3D400000100",
                "tray_uuid": "F00DFEEDF00DFEEDF00DFEEDF00DFEED",
                "remain": -1,
            }
        },
    },
    {
        "s": 222.7,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {
            "0.0": {
                "id": "0",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "C12E1FFF",
                "tray_info_idx": "GFA00",
                "tag_uid": "A1B2C3D400000100",
                "tray_uuid": "F00DFEEDF00DFEEDF00DFEEDF00DFEED",
                "remain": -1,
            }
        },
    },
    {
        "s": 224,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "0",
            "insert_flag": True,
        },
        "trays": {
            "0.0": {
                "id": "0",
                "state": 27,
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "C12E1FFF",
                "tray_info_idx": "GFA00",
                "tag_uid": "A1B2C3D400000100",
                "tray_uuid": "F00DFEEDF00DFEEDF00DFEEDF00DFEED",
                "remain": -1,
            }
        },
    },
    {
        "s": 235.3,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "0",
            "insert_flag": True,
        },
        "trays": {
            "0.0": {
                "id": "0",
                "state": 27,
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "C12E1FFF",
                "tray_info_idx": "GFA00",
                "tag_uid": "A1B2C3D400000100",
                "tray_uuid": "F00DFEEDF00DFEEDF00DFEEDF00DFEED",
                "remain": 63,
            }
        },
    },
    {
        "s": 239.9,
        "top": {
            "ams_exist_bits": "13",
            "tray_exist_bits": "100ff",
            "power_on_flag": False,
            "tray_now": "255",
            "insert_flag": True,
        },
        "trays": {
            "0.0": {
                "id": "0",
                "state": 11,
                "tray_type": "PLA",
                "tray_sub_brands": "PLA Basic",
                "tray_color": "C12E1FFF",
                "tray_info_idx": "GFA00",
                "tag_uid": "A1B2C3D400000100",
                "tray_uuid": "F00DFEEDF00DFEEDF00DFEEDF00DFEED",
                "remain": 63,
            }
        },
    },
]


def h2c_pushes() -> list[tuple[float, dict]]:
    """Each recorded push as ``(seconds, ams_data)``, with every tray, as the printer sent it."""
    trays: dict[str, dict] = {}
    pushes = []
    for delta in H2C_AMS_LOAD_DELTAS:
        trays.update(copy.deepcopy(delta["trays"]))
        units: dict[str, list] = {}
        for key, tray in trays.items():
            units.setdefault(key.split(".")[0], []).append(copy.deepcopy(tray))
        ams = [{"id": unit_id, "tray": unit_trays} for unit_id, unit_trays in units.items()]
        pushes.append((delta["s"], {**delta["top"], "ams": ams}))
    return pushes
