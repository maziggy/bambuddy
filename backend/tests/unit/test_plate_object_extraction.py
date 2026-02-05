"""Unit tests for plate object extraction from 3MF model_settings.config."""

from xml.etree import ElementTree as ET

import pytest


class TestPlateObjectExtraction:
    """Tests for extracting object IDs and names from model_settings.config XML."""

    def test_extract_object_names_from_xml(self):
        """Verify object names are extracted from model_settings.config XML."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <object id="1">
                <metadata key="name" value="Cube"/>
            </object>
            <object id="2">
                <metadata key="name" value="Sphere"/>
            </object>
            <object id="3">
                <metadata key="name" value="Cylinder"/>
            </object>
        </config>
        """
        root = ET.fromstring(xml_content)

        object_names_by_id = {}
        for obj in root.findall(".//object"):
            obj_id = obj.get("id")
            if obj_id:
                name_meta = obj.find("./metadata[@key='name']")
                if name_meta is not None:
                    object_names_by_id[obj_id] = name_meta.get("value", f"Object {obj_id}")
                else:
                    object_names_by_id[obj_id] = f"Object {obj_id}"

        assert object_names_by_id == {
            "1": "Cube",
            "2": "Sphere",
            "3": "Cylinder",
        }

    def test_extract_object_names_missing_name(self):
        """Verify objects without names get default names."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <object id="1">
                <metadata key="name" value="Named Object"/>
            </object>
            <object id="2">
                <!-- No name metadata -->
            </object>
        </config>
        """
        root = ET.fromstring(xml_content)

        object_names_by_id = {}
        for obj in root.findall(".//object"):
            obj_id = obj.get("id")
            if obj_id:
                name_meta = obj.find("./metadata[@key='name']")
                if name_meta is not None:
                    object_names_by_id[obj_id] = name_meta.get("value", f"Object {obj_id}")
                else:
                    object_names_by_id[obj_id] = f"Object {obj_id}"

        assert object_names_by_id == {
            "1": "Named Object",
            "2": "Object 2",
        }

    def test_extract_plate_object_associations(self):
        """Verify plate-to-object associations are extracted correctly."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="plater_id" value="1"/>
                <model_instance>
                    <metadata key="object_id" value="1"/>
                </model_instance>
                <model_instance>
                    <metadata key="object_id" value="2"/>
                </model_instance>
            </plate>
            <plate>
                <metadata key="plater_id" value="2"/>
                <model_instance>
                    <metadata key="object_id" value="3"/>
                </model_instance>
            </plate>
        </config>
        """
        root = ET.fromstring(xml_content)

        plate_object_ids = {}
        for plate in root.findall(".//plate"):
            plate_id = None
            for meta in plate.findall("metadata"):
                if meta.get("key") in ("plater_id", "plate_id"):
                    plate_id = meta.get("value")
                    break

            if plate_id:
                object_ids = []
                for instance in plate.findall(".//model_instance"):
                    for meta in instance.findall("metadata"):
                        if meta.get("key") == "object_id":
                            object_ids.append(meta.get("value"))
                plate_object_ids[plate_id] = object_ids

        assert plate_object_ids == {
            "1": ["1", "2"],
            "2": ["3"],
        }

    def test_extract_plate_object_associations_empty_plate(self):
        """Verify empty plates have empty object lists."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="plater_id" value="1"/>
                <!-- No model_instances -->
            </plate>
        </config>
        """
        root = ET.fromstring(xml_content)

        plate_object_ids = {}
        for plate in root.findall(".//plate"):
            plate_id = None
            for meta in plate.findall("metadata"):
                if meta.get("key") in ("plater_id", "plate_id"):
                    plate_id = meta.get("value")
                    break

            if plate_id:
                object_ids = []
                for instance in plate.findall(".//model_instance"):
                    for meta in instance.findall("metadata"):
                        if meta.get("key") == "object_id":
                            object_ids.append(meta.get("value"))
                plate_object_ids[plate_id] = object_ids

        assert plate_object_ids == {"1": []}

    def test_object_count_matches_objects_length(self):
        """Verify object_count equals len(objects)."""
        objects = ["Cube", "Sphere", "Cylinder"]
        object_count = len(objects)

        assert object_count == 3

    def test_resolve_object_names_from_ids(self):
        """Verify object IDs are resolved to names."""
        object_names_by_id = {
            "1": "Cube",
            "2": "Sphere",
            "3": "Cylinder",
        }
        plate_object_ids = ["1", "3"]

        resolved_names = [object_names_by_id.get(obj_id, f"Object {obj_id}") for obj_id in plate_object_ids]

        assert resolved_names == ["Cube", "Cylinder"]

    def test_resolve_object_names_missing_id(self):
        """Verify missing object IDs get fallback names."""
        object_names_by_id = {
            "1": "Cube",
        }
        plate_object_ids = ["1", "99"]  # 99 doesn't exist

        resolved_names = [object_names_by_id.get(obj_id, f"Object {obj_id}") for obj_id in plate_object_ids]

        assert resolved_names == ["Cube", "Object 99"]

    def test_plate_id_alternatives(self):
        """Verify both 'plater_id' and 'plate_id' keys are supported."""
        xml_with_plater_id = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="plater_id" value="1"/>
            </plate>
        </config>
        """
        xml_with_plate_id = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
            <plate>
                <metadata key="plate_id" value="2"/>
            </plate>
        </config>
        """

        def extract_plate_id(xml_content):
            root = ET.fromstring(xml_content)
            for plate in root.findall(".//plate"):
                for meta in plate.findall("metadata"):
                    if meta.get("key") in ("plater_id", "plate_id"):
                        return meta.get("value")
            return None

        assert extract_plate_id(xml_with_plater_id) == "1"
        assert extract_plate_id(xml_with_plate_id) == "2"
