"""Unit tests for the shared tag-conflict 409 (#3110)."""

from backend.app.services.tag_conflict import tag_already_linked


class TestTagAlreadyLinked:
    """Both inventory modes refuse a taken tag through this one constructor.

    They used to answer the same situation with two different sentences, only
    one of which named the spool holding the tag, and neither machine-readable.
    """

    def test_carries_the_holder_id_a_client_needs_to_offer_a_move(self):
        exc = tag_already_linked("tag_uid", 42)

        assert exc.status_code == 409
        assert exc.detail["code"] == "tag_already_linked"
        assert exc.detail["spool_id"] == 42
        assert exc.detail["field"] == "tag_uid"

    def test_names_which_identifier_collided(self):
        # A client that offers to move the tag has to know which of the two
        # columns it is moving; the id alone does not say.
        assert tag_already_linked("tray_uuid", 7).detail["field"] == "tray_uuid"

    def test_the_english_message_names_the_spool_for_non_ui_clients(self):
        # curl and scripts never reach the i18n layer, so `message` has to
        # stand on its own -- the old built-in sentence said only "another
        # active spool" and dropped the id it had already loaded.
        assert tag_already_linked("tag_uid", 42).detail["message"] == "Tag UID is already linked to spool 42"
        assert tag_already_linked("tray_uuid", 42).detail["message"] == "Tray UUID is already linked to spool 42"

    def test_both_fields_produce_the_same_code(self):
        # One code, so the frontend needs one i18n key rather than branching
        # on which endpoint answered.
        assert tag_already_linked("tag_uid", 1).detail["code"] == tag_already_linked("tray_uuid", 2).detail["code"]
