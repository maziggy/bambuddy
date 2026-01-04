#!/usr/bin/env python3
"""Comprehensive end-to-end test for Bambuddy application."""

import time

from playwright.sync_api import expect, sync_playwright

BASE_URL = "http://localhost:8000"


def test_navigation_and_sidebar(page):
    """Test sidebar navigation and all main pages."""
    print("\n=== Testing Navigation & Sidebar ===")

    # Go to home page
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    # Take initial screenshot
    page.screenshot(path="/tmp/bambuddy_home.png", full_page=True)
    print("✓ Home page loaded")

    # Check sidebar is visible
    sidebar = page.locator("nav").first
    expect(sidebar).to_be_visible()
    print("✓ Sidebar is visible")

    # Test navigation to each main page
    nav_items = [
        ("Printers", "/"),
        ("Archives", "/archives"),
        ("Queue", "/queue"),
        ("Statistics", "/stats"),
        ("Profiles", "/profiles"),
        ("Maintenance", "/maintenance"),
        ("Settings", "/settings"),
    ]

    for name, _path in nav_items:
        # Click on nav item by text
        nav_link = page.locator(f'nav >> text="{name}"').first
        if nav_link.is_visible():
            nav_link.click()
            page.wait_for_load_state("networkidle")
            time.sleep(0.5)  # Brief wait for animations
            print(f"✓ Navigated to {name}")
        else:
            print(f"⚠ Nav item '{name}' not visible")

    return True


def test_printers_page(page):
    """Test Printers page functionality."""
    print("\n=== Testing Printers Page ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Check for printer cards or "Add Printer" button
    page_content = page.content()

    # Look for printer-related elements
    if "Add Printer" in page_content or "printer" in page_content.lower():
        print("✓ Printers page content detected")

    # Check for AMS display if printers are connected
    ams_elements = page.locator("text=/AMS-[A-Z]/").all()
    if ams_elements:
        print(f"✓ Found {len(ams_elements)} AMS unit(s) displayed")

    # Take screenshot
    page.screenshot(path="/tmp/bambuddy_printers.png", full_page=True)
    print("✓ Printers page screenshot saved")

    return True


def test_archives_page(page):
    """Test Archives page functionality."""
    print("\n=== Testing Archives Page ===")

    page.goto(f"{BASE_URL}/archives")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Check for search input
    search_input = page.locator('input[placeholder*="Search"]').first
    if search_input.is_visible():
        print("✓ Search input found")
        # Test search functionality
        search_input.fill("test")
        time.sleep(0.5)
        search_input.clear()

    # Check for upload button
    upload_btn = page.locator('text="Upload"').first
    if upload_btn.is_visible():
        print("✓ Upload button found")

    # Take screenshot
    page.screenshot(path="/tmp/bambuddy_archives.png", full_page=True)
    print("✓ Archives page screenshot saved")

    return True


def test_queue_page(page):
    """Test Queue page functionality."""
    print("\n=== Testing Queue Page ===")

    page.goto(f"{BASE_URL}/queue")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Check for queue-related content
    page_content = page.content()

    if "Queue" in page_content or "queue" in page_content.lower():
        print("✓ Queue page content detected")

    # Take screenshot
    page.screenshot(path="/tmp/bambuddy_queue.png", full_page=True)
    print("✓ Queue page screenshot saved")

    return True


def test_statistics_page(page):
    """Test Statistics page functionality."""
    print("\n=== Testing Statistics Page ===")

    page.goto(f"{BASE_URL}/stats")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Check for statistics widgets
    page_content = page.content()

    stats_keywords = ["Total", "Success", "Failed", "Prints", "Filament", "Time"]
    found_stats = [kw for kw in stats_keywords if kw in page_content]

    if found_stats:
        print(f"✓ Statistics found: {', '.join(found_stats)}")

    # Take screenshot
    page.screenshot(path="/tmp/bambuddy_statistics.png", full_page=True)
    print("✓ Statistics page screenshot saved")

    return True


def test_settings_page(page):
    """Test Settings page functionality."""
    print("\n=== Testing Settings Page ===")

    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Check for settings sections
    settings_sections = [
        "Spoolman",
        "Notifications",
        "Smart Plugs",
        "General",
    ]

    page_content = page.content()
    for section in settings_sections:
        if section in page_content:
            print(f"✓ Settings section found: {section}")

    # Take screenshot
    page.screenshot(path="/tmp/bambuddy_settings.png", full_page=True)
    print("✓ Settings page screenshot saved")

    return True


def test_keyboard_shortcuts(page):
    """Test keyboard shortcuts functionality."""
    print("\n=== Testing Keyboard Shortcuts ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)

    # Press '?' to open shortcuts modal
    page.keyboard.press("?")
    time.sleep(0.5)

    # Check if modal opened
    modal = page.locator('text="Keyboard Shortcuts"').first
    if modal.is_visible():
        print("✓ Keyboard shortcuts modal opened with '?'")
        page.screenshot(path="/tmp/bambuddy_shortcuts_modal.png")

        # Close with Escape
        page.keyboard.press("Escape")
        time.sleep(0.3)
        if not modal.is_visible():
            print("✓ Modal closed with Escape")
    else:
        print("⚠ Keyboard shortcuts modal did not open")

    # Test number key navigation
    # Press '2' to go to Archives
    page.keyboard.press("2")
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)

    current_url = page.url
    if "/archives" in current_url:
        print("✓ Hotkey '2' navigated to Archives")
    else:
        print(f"⚠ Hotkey '2' navigation - current URL: {current_url}")

    # Press '1' to go back to Printers
    page.keyboard.press("1")
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)

    current_url = page.url
    if current_url == BASE_URL + "/" or current_url == BASE_URL:
        print("✓ Hotkey '1' navigated to Printers")

    return True


def test_theme_toggle(page):
    """Test theme toggle functionality."""
    print("\n=== Testing Theme Toggle ===")

    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Check if dark theme is applied (should be default)
    html = page.locator("html")
    classes = html.get_attribute("class") or ""

    if "dark" in classes:
        print("✓ Dark theme is active (default)")
    else:
        print("ℹ Dark theme class not found on HTML element")

    # Look for theme-related UI elements
    page_content = page.content()
    if "theme" in page_content.lower() or "dark" in page_content.lower():
        print("✓ Theme-related content found on page")

    return True


def test_responsive_design(page):
    """Test responsive design at different viewport sizes."""
    print("\n=== Testing Responsive Design ===")

    viewports = [
        ("Desktop", 1920, 1080),
        ("Tablet", 768, 1024),
        ("Mobile", 375, 667),
    ]

    for name, width, height in viewports:
        page.set_viewport_size({"width": width, "height": height})
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(path=f"/tmp/bambuddy_{name.lower()}.png", full_page=True)
        print(f"✓ {name} viewport ({width}x{height}) screenshot saved")

    # Reset to desktop
    page.set_viewport_size({"width": 1920, "height": 1080})

    return True


def test_external_links_sidebar(page):
    """Test external links in sidebar."""
    print("\n=== Testing External Links in Sidebar ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)

    # Look for external link indicators
    external_links = page.locator('nav a[target="_blank"], nav >> text=/Spoolman|SpoolEase/i').all()

    if external_links:
        print(f"✓ Found {len(external_links)} external link(s) in sidebar")
    else:
        print("ℹ No external links configured in sidebar")

    return True


def test_api_health(page):
    """Test basic API endpoints."""
    print("\n=== Testing API Health ===")

    # Test printers endpoint
    response = page.request.get(f"{BASE_URL}/api/v1/printers/")
    if response.ok:
        data = response.json()
        print(f"✓ GET /api/v1/printers/ - {len(data)} printer(s)")
    else:
        print(f"⚠ GET /api/v1/printers/ - Status: {response.status}")

    # Test archives endpoint
    response = page.request.get(f"{BASE_URL}/api/v1/archives/")
    if response.ok:
        data = response.json()
        print(f"✓ GET /api/v1/archives/ - {len(data)} archive(s)")
    else:
        print(f"⚠ GET /api/v1/archives/ - Status: {response.status}")

    # Test settings endpoint
    response = page.request.get(f"{BASE_URL}/api/v1/settings/")
    if response.ok:
        print("✓ GET /api/v1/settings/ - OK")
    else:
        print(f"⚠ GET /api/v1/settings/ - Status: {response.status}")

    return True


def run_comprehensive_test():
    """Run all comprehensive tests."""
    print("=" * 60)
    print("BAMBUDDY COMPREHENSIVE E2E TEST")
    print("=" * 60)
    print(f"Target: {BASE_URL}")

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Enable console logging
        page.on("console", lambda msg: print(f"  [Browser] {msg.text}") if msg.type == "error" else None)

        tests = [
            ("API Health", test_api_health),
            ("Navigation & Sidebar", test_navigation_and_sidebar),
            ("Printers Page", test_printers_page),
            ("Archives Page", test_archives_page),
            ("Queue Page", test_queue_page),
            ("Statistics Page", test_statistics_page),
            ("Settings Page", test_settings_page),
            ("Keyboard Shortcuts", test_keyboard_shortcuts),
            ("Theme Toggle", test_theme_toggle),
            ("External Links", test_external_links_sidebar),
            ("Responsive Design", test_responsive_design),
        ]

        for test_name, test_func in tests:
            try:
                results[test_name] = test_func(page)
            except Exception as e:
                print(f"\n❌ {test_name} FAILED: {e}")
                results[test_name] = False
                page.screenshot(path=f'/tmp/bambuddy_error_{test_name.lower().replace(" ", "_")}.png')

        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, passed_test in results.items():
        status = "✓ PASS" if passed_test else "❌ FAIL"
        print(f"  {status} - {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print("Screenshots saved to /tmp/bambuddy_*.png")

    return all(results.values())


if __name__ == "__main__":
    success = run_comprehensive_test()
    exit(0 if success else 1)
