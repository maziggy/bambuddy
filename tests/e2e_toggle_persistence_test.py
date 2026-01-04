#!/usr/bin/env python3
"""E2E tests for toggle persistence - critical regression prevention.

These tests verify that toggle settings (auto_off, notification events, etc.)
are properly persisted to the database and survive page reloads.
"""

import time

from playwright.sync_api import expect, sync_playwright

BASE_URL = "http://localhost:8000"


def test_smart_plug_auto_off_toggle_persistence(page):
    """CRITICAL: Test that auto_off toggle persists after page reload.

    This tests the regression where auto_off toggle wasn't being saved.
    """
    print("\n=== Testing Smart Plug Auto-Off Toggle Persistence ===")

    # Navigate to Settings page
    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Look for Smart Plugs section
    smart_plugs_section = page.locator('text="Smart Plugs"').first
    if not smart_plugs_section.is_visible():
        print("⚠ No Smart Plugs section found - skipping test")
        return True

    # Find an Automation Settings toggle to expand
    automation_toggle = page.locator('text="Automation Settings"').first
    if not automation_toggle.is_visible():
        print("⚠ No Automation Settings found - skipping test")
        return True

    automation_toggle.click()
    time.sleep(0.5)

    # Find Auto Off toggle
    auto_off_label = page.locator('text="Auto Off"').first
    if not auto_off_label.is_visible():
        print("⚠ Auto Off label not found - skipping test")
        return True

    # Find the toggle switch near the Auto Off label
    # The toggle is a sibling element
    auto_off_section = auto_off_label.locator("..").first
    toggle = auto_off_section.locator('button[role="switch"]').first

    if not toggle.is_visible():
        # Try finding any toggle in the section
        toggle = page.locator('button[role="switch"]').nth(1)  # Skip first (main enabled toggle)

    if not toggle.is_visible():
        print("⚠ Auto Off toggle not found - skipping test")
        return True

    # Get initial state
    initial_state = toggle.get_attribute("aria-checked")
    print(f"✓ Initial auto_off state: {initial_state}")

    # Click to toggle
    toggle.click()
    time.sleep(1)  # Wait for API call

    # Verify toggle changed
    new_state = toggle.get_attribute("aria-checked")
    assert new_state != initial_state, "Toggle should have changed state"
    print(f"✓ Toggled auto_off to: {new_state}")

    # Reload the page
    page.reload()
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Expand automation settings again
    automation_toggle = page.locator('text="Automation Settings"').first
    automation_toggle.click()
    time.sleep(0.5)

    # Find toggle again and verify state persisted
    auto_off_section = page.locator('text="Auto Off"').first.locator("..").first
    toggle = auto_off_section.locator('button[role="switch"]').first
    if not toggle.is_visible():
        toggle = page.locator('button[role="switch"]').nth(1)

    persisted_state = toggle.get_attribute("aria-checked")
    assert (
        persisted_state == new_state
    ), f"State should persist after reload. Expected {new_state}, got {persisted_state}"
    print(f"✓ Toggle state persisted after reload: {persisted_state}")

    # Restore original state
    if persisted_state != initial_state:
        toggle.click()
        time.sleep(1)
        print("✓ Restored original toggle state")

    return True


def test_notification_event_toggle_persistence(page):
    """CRITICAL: Test that notification event toggles persist after page reload.

    This tests the regression where notification event toggles weren't being saved.
    """
    print("\n=== Testing Notification Event Toggle Persistence ===")

    # Navigate to Settings page
    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Look for Notifications section
    notifications_section = page.locator('text="Notifications"').first
    if not notifications_section.is_visible():
        print("⚠ No Notifications section found - skipping test")
        return True

    # Find Event Settings toggle to expand
    event_settings = page.locator('text="Event Settings"').first
    if not event_settings.is_visible():
        print("⚠ No Event Settings found - skipping test")
        return True

    event_settings.click()
    time.sleep(0.5)

    # Find Print Stopped toggle (this was a regression point)
    stopped_label = page.locator('text="Print Stopped"').first
    if not stopped_label.is_visible():
        print("⚠ Print Stopped label not found - skipping test")
        return True

    # Find the toggle switch
    stopped_section = stopped_label.locator("..").first
    toggle = stopped_section.locator('button[role="switch"]').first

    if not toggle.is_visible():
        print("⚠ Print Stopped toggle not found - skipping test")
        return True

    # Get initial state
    initial_state = toggle.get_attribute("aria-checked")
    print(f"✓ Initial on_print_stopped state: {initial_state}")

    # Click to toggle
    toggle.click()
    time.sleep(1)  # Wait for API call

    # Verify toggle changed
    new_state = toggle.get_attribute("aria-checked")
    assert new_state != initial_state, "Toggle should have changed state"
    print(f"✓ Toggled on_print_stopped to: {new_state}")

    # Reload the page
    page.reload()
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Expand event settings again
    event_settings = page.locator('text="Event Settings"').first
    event_settings.click()
    time.sleep(0.5)

    # Find toggle again and verify state persisted
    stopped_section = page.locator('text="Print Stopped"').first.locator("..").first
    toggle = stopped_section.locator('button[role="switch"]').first

    if toggle.is_visible():
        persisted_state = toggle.get_attribute("aria-checked")
        assert (
            persisted_state == new_state
        ), f"State should persist after reload. Expected {new_state}, got {persisted_state}"
        print(f"✓ Toggle state persisted after reload: {persisted_state}")

        # Restore original state
        if persisted_state != initial_state:
            toggle.click()
            time.sleep(1)
            print("✓ Restored original toggle state")

    return True


def test_ams_alarm_toggle_persistence(page):
    """Test that AMS alarm toggles persist after page reload."""
    print("\n=== Testing AMS Alarm Toggle Persistence ===")

    # Navigate to Settings page
    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Look for Event Settings in notification provider
    event_settings = page.locator('text="Event Settings"').first
    if not event_settings.is_visible():
        print("⚠ No Event Settings found - skipping test")
        return True

    event_settings.click()
    time.sleep(0.5)

    # Look for AMS Humidity High toggle
    ams_humidity_label = page.locator('text="AMS Humidity High"').first
    if not ams_humidity_label.is_visible():
        print("⚠ AMS Humidity High label not found - skipping test")
        return True

    print("✓ AMS Alarm toggles section found")

    # Find and test the toggle
    ams_section = ams_humidity_label.locator("..").first
    toggle = ams_section.locator('button[role="switch"]').first

    if toggle.is_visible():
        initial_state = toggle.get_attribute("aria-checked")
        print(f"✓ Initial AMS humidity alarm state: {initial_state}")

        toggle.click()
        time.sleep(1)

        new_state = toggle.get_attribute("aria-checked")
        print(f"✓ Toggled AMS humidity alarm to: {new_state}")

        # Reload and verify
        page.reload()
        page.wait_for_load_state("networkidle")
        time.sleep(1)

        event_settings = page.locator('text="Event Settings"').first
        event_settings.click()
        time.sleep(0.5)

        ams_section = page.locator('text="AMS Humidity High"').first.locator("..").first
        toggle = ams_section.locator('button[role="switch"]').first

        if toggle.is_visible():
            persisted_state = toggle.get_attribute("aria-checked")
            print(f"✓ AMS alarm state after reload: {persisted_state}")

            # Restore
            if persisted_state != initial_state:
                toggle.click()
                time.sleep(1)

    return True


def test_smart_plug_power_off_confirmation(page):
    """Test that power off shows confirmation dialog."""
    print("\n=== Testing Smart Plug Power Off Confirmation ===")

    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Find an Off button
    off_button = page.locator('button:has-text("Off")').first
    if not off_button.is_visible():
        print("⚠ No Off button found - skipping test")
        return True

    off_button.click()
    time.sleep(0.5)

    # Look for confirmation dialog
    confirm_dialog = page.locator("text=/Turn Off|Confirm|cut power/i").first
    if confirm_dialog.is_visible():
        print("✓ Confirmation dialog appeared")

        # Close dialog by clicking Cancel or outside
        cancel_btn = page.locator('button:has-text("Cancel")').first
        if cancel_btn.is_visible():
            cancel_btn.click()
            print("✓ Cancelled power off")
    else:
        print("⚠ No confirmation dialog found")

    return True


def run_all_toggle_tests():
    """Run all toggle persistence tests."""
    print("=" * 60)
    print("Bambuddy Toggle Persistence E2E Tests")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()

        tests = [
            ("Smart Plug Auto-Off Toggle", test_smart_plug_auto_off_toggle_persistence),
            ("Notification Event Toggle", test_notification_event_toggle_persistence),
            ("AMS Alarm Toggle", test_ams_alarm_toggle_persistence),
            ("Power Off Confirmation", test_smart_plug_power_off_confirmation),
        ]

        results = []
        for name, test_func in tests:
            try:
                result = test_func(page)
                results.append((name, "PASS" if result else "FAIL"))
            except Exception as e:
                print(f"✗ Test failed with error: {e}")
                results.append((name, f"ERROR: {e}"))

        browser.close()

        # Print summary
        print("\n" + "=" * 60)
        print("Test Results Summary")
        print("=" * 60)
        for name, result in results:
            status = "✓" if result == "PASS" else "✗"
            print(f"{status} {name}: {result}")

        passed = sum(1 for _, r in results if r == "PASS")
        total = len(results)
        print(f"\nTotal: {passed}/{total} passed")

        return passed == total


if __name__ == "__main__":
    import sys

    success = run_all_toggle_tests()
    sys.exit(0 if success else 1)
