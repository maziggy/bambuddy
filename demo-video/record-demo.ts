import { chromium, Page, Browser, BrowserContext } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Configuration
const CONFIG = {
  baseUrl: process.env.DEMO_URL || 'http://localhost:8000',
  headless: process.env.HEADLESS === 'true',
  slowMo: 50, // Slow down actions for visibility
  viewportWidth: 1920,
  viewportHeight: 1080,
  outputDir: path.join(__dirname, 'output'),
};

// Timing helpers (in ms)
const TIMING = {
  pageLoad: 1500,      // Wait after page navigation
  shortPause: 500,     // Brief pause between actions
  mediumPause: 1000,   // Standard pause for visibility
  longPause: 2000,     // Longer pause for important features
  modalOpen: 800,      // Wait for modal animations
  scrollPause: 600,    // Pause after scrolling
};

async function wait(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function scrollDown(page: Page, pixels: number = 300): Promise<void> {
  await page.mouse.wheel(0, pixels);
  await wait(TIMING.scrollPause);
}

async function scrollToTop(page: Page): Promise<void> {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
  await wait(TIMING.scrollPause);
}

async function hoverElement(page: Page, selector: string): Promise<void> {
  const element = page.locator(selector).first();
  if (await element.isVisible()) {
    await element.hover();
    await wait(TIMING.shortPause);
  }
}

async function clickIfVisible(page: Page, selector: string): Promise<boolean> {
  const element = page.locator(selector).first();
  if (await element.isVisible()) {
    await element.click();
    return true;
  }
  return false;
}

async function closeModalIfOpen(page: Page): Promise<void> {
  // Try to close any open modal by pressing Escape
  await page.keyboard.press('Escape');
  await wait(TIMING.shortPause);
}

async function blurSensitiveContent(page: Page): Promise<void> {
  // Use JavaScript to find and blur email addresses
  await page.evaluate(() => {
    // Find all spans and check for email patterns
    document.querySelectorAll('span').forEach(el => {
      const text = el.textContent || '';
      // Check if this specific element (not children) contains an email
      if (el.childNodes.length === 1 && el.childNodes[0].nodeType === Node.TEXT_NODE) {
        if (text.includes('@') && text.includes('.')) {
          (el as HTMLElement).style.filter = 'blur(6px)';
          (el as HTMLElement).style.userSelect = 'none';
        }
      }
    });

    // Also find "Connected as" text and blur the next sibling span
    document.querySelectorAll('span').forEach(el => {
      if (el.textContent?.includes('Connected as')) {
        const emailSpan = el.querySelector('span');
        if (emailSpan) {
          (emailSpan as HTMLElement).style.filter = 'blur(6px)';
          (emailSpan as HTMLElement).style.userSelect = 'none';
        }
      }
    });
  });
}

// ============================================================================
// Page Scenarios
// ============================================================================

async function demoPrintersPage(page: Page): Promise<void> {
  console.log('📷 Demonstrating Printers page...');
  await page.goto(CONFIG.baseUrl);
  await wait(TIMING.pageLoad);

  // Hover over printer cards to show interactions
  const printerCards = page.locator('.group').filter({ has: page.locator('img') });
  const cardCount = await printerCards.count();
  console.log(`   Found ${cardCount} printer cards`);

  for (let i = 0; i < Math.min(cardCount, 2); i++) {
    const card = printerCards.nth(i);
    if (await card.isVisible()) {
      await card.hover();
      await wait(TIMING.mediumPause);

      // Try clicking on card to expand/show details
      await card.click();
      await wait(TIMING.mediumPause);
    }
  }

  // Look for AMS section and hover over slots
  const amsSlots = page.locator('[class*="ams"], [class*="AMS"]').first();
  if (await amsSlots.isVisible()) {
    await amsSlots.hover();
    await wait(TIMING.mediumPause);
  }

  // Try to open camera modal
  const cameraIcon = page.locator('svg[class*="lucide-video"], button:has(svg)').first();
  if (await cameraIcon.isVisible()) {
    await cameraIcon.click();
    await wait(TIMING.longPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Try to open MQTT debug modal
  const debugButton = page.locator('button:has-text("Debug"), button:has-text("MQTT")').first();
  if (await debugButton.isVisible()) {
    await debugButton.click();
    await wait(TIMING.longPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Scroll to show more printers
  await scrollDown(page, 400);
  await wait(TIMING.mediumPause);
  await scrollToTop(page);
}

async function demoArchivesPage(page: Page): Promise<void> {
  console.log('📁 Demonstrating Archives page...');
  await page.goto(`${CONFIG.baseUrl}/archives`);
  await wait(TIMING.pageLoad);

  // Show view mode toggle (grid/list/calendar)
  const viewToggle = page.locator('button:has(svg[class*="grid"]), button:has(svg[class*="list"])');
  if (await viewToggle.first().isVisible()) {
    await viewToggle.first().click();
    await wait(TIMING.mediumPause);
    await viewToggle.first().click(); // Toggle back
    await wait(TIMING.shortPause);
  }

  // Use search
  const searchInput = page.locator('input[placeholder*="Search"], input[type="search"]').first();
  if (await searchInput.isVisible()) {
    await searchInput.click();
    await searchInput.fill('engine');
    await wait(TIMING.longPause);
    await searchInput.clear();
    await wait(TIMING.shortPause);
  }

  // Show filter dropdowns
  const filterButtons = page.locator('button:has-text("Printer"), button:has-text("Material"), button:has-text("Filter")');
  if (await filterButtons.first().isVisible()) {
    await filterButtons.first().click();
    await wait(TIMING.mediumPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Right-click to show context menu
  const archiveCard = page.locator('.group').filter({ has: page.locator('img') }).first();
  if (await archiveCard.isVisible()) {
    await archiveCard.click({ button: 'right' });
    await wait(TIMING.longPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Click on archive to open edit modal
  if (await archiveCard.isVisible()) {
    await archiveCard.dblclick();
    await wait(TIMING.longPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Scroll to show more archives
  await scrollDown(page, 500);
  await wait(TIMING.mediumPause);
  await scrollToTop(page);
}

async function demoQueuePage(page: Page): Promise<void> {
  console.log('📋 Demonstrating Queue page...');
  await page.goto(`${CONFIG.baseUrl}/queue`);
  await wait(TIMING.pageLoad);

  // Show filter dropdowns
  const printerFilter = page.locator('button:has-text("Printer"), select').first();
  if (await printerFilter.isVisible()) {
    await printerFilter.click();
    await wait(TIMING.mediumPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Show sort controls
  const sortButton = page.locator('button:has-text("Sort"), button:has(svg[class*="arrow"])').first();
  if (await sortButton.isVisible()) {
    await sortButton.click();
    await wait(TIMING.mediumPause);
  }

  // Hover over queue items to show drag handles
  const queueItems = page.locator('[draggable="true"], .group').first();
  if (await queueItems.isVisible()) {
    await queueItems.hover();
    await wait(TIMING.mediumPause);
  }

  // Scroll through queue
  await scrollDown(page, 300);
  await wait(TIMING.mediumPause);
  await scrollToTop(page);
}

async function demoStatsPage(page: Page): Promise<void> {
  console.log('📊 Demonstrating Stats page...');
  await page.goto(`${CONFIG.baseUrl}/stats`);
  await wait(TIMING.pageLoad);

  // Let charts animate
  await wait(TIMING.longPause);

  // Show export dropdown
  const exportButton = page.locator('button:has-text("Export"), button:has(svg[class*="download"])').first();
  if (await exportButton.isVisible()) {
    await exportButton.click();
    await wait(TIMING.mediumPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Scroll through stats widgets
  await scrollDown(page, 400);
  await wait(TIMING.mediumPause);
  await scrollDown(page, 400);
  await wait(TIMING.mediumPause);
  await scrollDown(page, 400);
  await wait(TIMING.mediumPause);
  await scrollToTop(page);
}

async function demoProfilesPage(page: Page): Promise<void> {
  console.log('⚙️ Demonstrating Profiles page...');

  // Start blur loop BEFORE navigating
  let blurring = true;
  const blurLoop = async () => {
    while (blurring) {
      try {
        await page.evaluate(() => {
          document.querySelectorAll('span').forEach(el => {
            if (el.textContent?.includes('Connected as')) {
              const emailSpan = el.querySelector('span');
              if (emailSpan) {
                (emailSpan as HTMLElement).style.filter = 'blur(6px)';
              }
            }
          });
        });
      } catch { /* page might be navigating */ }
      await new Promise(r => setTimeout(r, 30));
    }
  };

  // Start blur loop in background
  const blurPromise = blurLoop();

  await page.goto(`${CONFIG.baseUrl}/profiles`);
  await wait(TIMING.pageLoad);

  // Show Cloud Profiles section
  await wait(TIMING.mediumPause);

  // Click on K-Profiles tab if available
  try {
    const kProfilesTab = page.locator('button:has-text("K-Profile"), button:has-text("K Profile")').first();
    if (await kProfilesTab.isVisible({ timeout: 1000 })) {
      await kProfilesTab.click({ timeout: 2000 });
      await wait(TIMING.mediumPause);
      await scrollDown(page, 300);
      await wait(TIMING.shortPause);
      await scrollToTop(page);
    }
  } catch { /* skip */ }

  // Click back to Cloud Profiles
  try {
    const cloudTab = page.locator('button:has-text("Cloud")').first();
    if (await cloudTab.isVisible({ timeout: 1000 })) {
      await cloudTab.click({ timeout: 2000 });
      await wait(TIMING.mediumPause);
    }
  } catch { /* skip */ }

  // Show preset filter types (if visible) - use force to bypass overlays
  const presetFilters = page.locator('button:has-text("Filament"), button:has-text("Process"), button:has-text("Machine")');
  for (let i = 0; i < 3; i++) {
    try {
      const filter = presetFilters.nth(i);
      if (await filter.isVisible({ timeout: 1000 })) {
        await filter.click({ force: true, timeout: 2000 });
        await wait(TIMING.shortPause);
      }
    } catch { /* skip if not visible or blocked */ }
  }

  await scrollDown(page, 300);
  await wait(TIMING.shortPause);
  await scrollToTop(page);

  // Stop blur loop
  blurring = false;
  await blurPromise;
}

async function demoMaintenancePage(page: Page): Promise<void> {
  console.log('🔧 Demonstrating Maintenance page...');
  await page.goto(`${CONFIG.baseUrl}/maintenance`);
  await wait(TIMING.pageLoad);

  // Show status tab (default)
  await wait(TIMING.mediumPause);

  // Expand a printer section if available
  const expandButton = page.locator('button:has(svg[class*="chevron"])').first();
  if (await expandButton.isVisible()) {
    await expandButton.click();
    await wait(TIMING.mediumPause);
  }

  // Scroll through status
  await scrollDown(page, 300);
  await wait(TIMING.shortPause);
  await scrollToTop(page);

  // Click Settings tab
  const settingsTab = page.locator('button:has-text("Settings"), [role="tab"]:has-text("Settings")').first();
  if (await settingsTab.isVisible()) {
    await settingsTab.click();
    await wait(TIMING.mediumPause);

    // Scroll through settings
    await scrollDown(page, 300);
    await wait(TIMING.shortPause);
    await scrollToTop(page);
  }

  // Go back to Status tab
  const statusTab = page.locator('button:has-text("Status"), [role="tab"]:has-text("Status")').first();
  if (await statusTab.isVisible()) {
    await statusTab.click();
    await wait(TIMING.shortPause);
  }
}

async function demoProjectsPage(page: Page): Promise<void> {
  console.log('📂 Demonstrating Projects page...');
  await page.goto(`${CONFIG.baseUrl}/projects`);
  await wait(TIMING.pageLoad);

  // Click through status filter buttons
  const statusFilters = ['Active', 'Completed', 'Archived', 'All'];
  for (const status of statusFilters) {
    const filterBtn = page.locator(`button:has-text("${status}")`).first();
    if (await filterBtn.isVisible()) {
      await filterBtn.click();
      await wait(TIMING.shortPause);
    }
  }

  // Click on a project to go to detail page
  const projectCard = page.locator('.group, [class*="project"]').filter({ has: page.locator('h3, h2') }).first();
  if (await projectCard.isVisible()) {
    await projectCard.click();
    await wait(TIMING.pageLoad);

    // Scroll through project detail
    await scrollDown(page, 300);
    await wait(TIMING.mediumPause);

    // Look for tabs in project detail (BOM, Attachments, Prints)
    const detailTabs = ['BOM', 'Attachments', 'Prints', 'Notes'];
    for (const tabName of detailTabs) {
      const tab = page.locator(`button:has-text("${tabName}"), [role="tab"]:has-text("${tabName}")`).first();
      if (await tab.isVisible()) {
        await tab.click();
        await wait(TIMING.mediumPause);
      }
    }

    await scrollToTop(page);
  }
}

async function demoSettingsPage(page: Page): Promise<void> {
  console.log('⚙️ Demonstrating Settings page...');
  await page.goto(`${CONFIG.baseUrl}/settings`);
  await wait(TIMING.pageLoad);

  // Define the 6 tabs to click through
  const tabs = ['General', 'Plugs', 'Notifications', 'Filament', 'API', 'Virtual'];

  for (const tabName of tabs) {
    const tab = page.locator(`button:has-text("${tabName}"), [role="tab"]:has-text("${tabName}")`).first();
    if (await tab.isVisible()) {
      await tab.click();
      await wait(TIMING.mediumPause);

      // Scroll through tab content
      await scrollDown(page, 300);
      await wait(TIMING.shortPause);
      await scrollToTop(page);
    }
  }

  // Go back to General tab and show a modal
  const generalTab = page.locator('button:has-text("General")').first();
  if (await generalTab.isVisible()) {
    await generalTab.click();
    await wait(TIMING.shortPause);
  }

  // Try to open backup modal
  const backupButton = page.locator('button:has-text("Backup")').first();
  if (await backupButton.isVisible()) {
    await backupButton.click();
    await wait(TIMING.longPause);
    await page.keyboard.press('Escape');
    await wait(TIMING.shortPause);
  }

  // Go to Plugs tab and show add modal
  const plugsTab = page.locator('button:has-text("Plugs")').first();
  if (await plugsTab.isVisible()) {
    await plugsTab.click();
    await wait(TIMING.shortPause);

    const addPlugButton = page.locator('button:has-text("Add"), button:has(svg[class*="plus"])').first();
    if (await addPlugButton.isVisible()) {
      await addPlugButton.click();
      await wait(TIMING.longPause);
      await page.keyboard.press('Escape');
      await wait(TIMING.shortPause);
    }
  }

  // Go to Notifications tab and show add modal
  const notifTab = page.locator('button:has-text("Notifications")').first();
  if (await notifTab.isVisible()) {
    await notifTab.click();
    await wait(TIMING.shortPause);

    const addNotifButton = page.locator('button:has-text("Add"), button:has(svg[class*="plus"])').first();
    if (await addNotifButton.isVisible()) {
      await addNotifButton.click();
      await wait(TIMING.longPause);
      await page.keyboard.press('Escape');
      await wait(TIMING.shortPause);
    }
  }

  await scrollToTop(page);
}

async function demoSystemPage(page: Page): Promise<void> {
  console.log('💻 Demonstrating System page...');
  await page.goto(`${CONFIG.baseUrl}/system`);
  await wait(TIMING.pageLoad);

  // Show system info
  await wait(TIMING.mediumPause);
  await scrollDown(page, 300);
  await wait(TIMING.shortPause);
  await scrollToTop(page);
}

// ============================================================================
// Main Recording Function
// ============================================================================

async function recordDemo(): Promise<void> {
  console.log('🎬 Starting Bambuddy demo recording...');
  console.log(`   URL: ${CONFIG.baseUrl}`);
  console.log(`   Resolution: ${CONFIG.viewportWidth}x${CONFIG.viewportHeight}`);
  console.log(`   Headless: ${CONFIG.headless}`);
  console.log('');

  const browser: Browser = await chromium.launch({
    headless: CONFIG.headless,
    slowMo: CONFIG.slowMo,
  });

  const context: BrowserContext = await browser.newContext({
    viewport: {
      width: CONFIG.viewportWidth,
      height: CONFIG.viewportHeight,
    },
    recordVideo: {
      dir: CONFIG.outputDir,
      size: {
        width: CONFIG.viewportWidth,
        height: CONFIG.viewportHeight,
      },
    },
  });

  const page: Page = await context.newPage();

  try {
    // Run through all page demos
    await demoPrintersPage(page);
    await demoArchivesPage(page);
    await demoQueuePage(page);
    await demoStatsPage(page);
    await demoProfilesPage(page);
    await demoMaintenancePage(page);
    await demoProjectsPage(page);
    await demoSettingsPage(page);
    await demoSystemPage(page);

    // Return to home page for closing shot
    console.log('🏠 Returning to home page...');
    await page.goto(CONFIG.baseUrl);
    await wait(TIMING.longPause);

    console.log('✅ Demo recording completed!');
  } catch (error) {
    console.error('❌ Error during recording:', error);
    throw error;
  } finally {
    await page.close();
    await context.close();
    await browser.close();
  }

  console.log(`\n📹 Video saved to: ${CONFIG.outputDir}/`);
  console.log('   (Playwright saves as .webm, convert with ffmpeg if needed)');
  console.log('   Example: ffmpeg -i video.webm -c:v libx264 demo.mp4');
}

// Run the recording
recordDemo().catch(console.error);
