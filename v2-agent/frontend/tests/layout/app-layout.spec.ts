import { expect, Page, test } from "@playwright/test";

const viewports = [
  { name: "mobile", width: 390, height: 844 },
  { name: "desktop", width: 1440, height: 1000 },
  { name: "wide", width: 2560, height: 1440 },
] as const;

const user = {
  Userid: 7,
  account_uuid: "layout-review-user",
  username: "验收用户",
  phone: "138****0000",
};

async function installBaseMocks(page: Page, authenticated = false) {
  await page.route(/\/v2-api\/csrf(?:\?|$)/, (route) => route.fulfill({
    json: { csrfToken: "a".repeat(40) },
  }));
  await page.route(/\/v2-api\/health(?:\?|$)/, (route) => route.fulfill({
    json: {
      status: "ok",
      model: "layout-review",
      vlmEnabled: true,
      accessProtectionEnabled: true,
      capabilities: { image: "ready", video: "ready", document: "ready" },
      limits: { maxUploadBytes: 25 * 1024 * 1024 },
    },
  }));
  await page.route("**/api/analytics/pageview", (route) => route.fulfill({
    status: 204,
    body: "",
  }));
  await page.route("**/api/me", (route) => route.fulfill({
    json: {
      status: "success",
      authenticated,
      user: authenticated ? user : null,
      counters: { image_detect: 0, video_detect: 0 },
    },
  }));

  if (!authenticated) return;
  await page.route("**/v2-api/history**", (route) => route.fulfill({
    json: { items: [], total: 0, filterCounts: {} },
  }));
  await page.route("**/api/history/image-detections**", (route) => route.fulfill({
    json: { status: "success", records: [], total: 0 },
  }));
  await page.route("**/api/history/video-detections**", (route) => route.fulfill({
    json: { status: "success", records: [], total: 0 },
  }));
}

async function installDeveloperMocks(page: Page) {
  await page.route("**/api/developer/account?**", (route) => route.fulfill({
    json: {
      status: "success",
      account: {
        userId: user.Userid,
        status: "active",
        freeTotal: 100,
        freeUsed: 39,
        freeReserved: 0,
        freeRemaining: 61,
        balanceFen: 11820,
        balanceCny: "118.20",
        balanceReservedFen: 0,
        availableBalanceFen: 11820,
        createdAt: "2026-08-01 10:00:00",
        updatedAt: "2026-08-08 10:00:00",
      },
      pricing: [
        { mode: "fast", name: "快速检测", unitPriceFen: 10, unitPriceCny: "0.10", enabled: true },
        { mode: "swarm", name: "Swarm 复核", unitPriceFen: 50, unitPriceCny: "0.50", enabled: true },
      ],
      modeSummary: {
        fast: { calls: 186, spendFen: 1860 },
        swarm: { calls: 24, spendFen: 1200 },
      },
      usage: {
        days: 30,
        summary: {
          totalCalls: 210,
          totalRequests: 210,
          v1Calls: 186,
          v2Calls: 24,
          billableRequests: 149,
          cacheHits: 12,
          promptTokens: 42000,
          completionTokens: 18210,
          totalTokens: 60210,
          lastEventAt: "2026-08-08 10:00:00",
        },
        byDay: [],
        byEndpoint: [],
        byModel: [],
        byKey: [],
        byPipeline: [],
      },
      recentTasks: [],
    },
  }));
  await page.route("**/api/developer/keys", (route) => route.fulfill({
    json: {
      status: "success",
      keys: [{
        id: 11,
        name: "生产环境",
        preview: "hj_live_••••9f2a",
        scopes: ["image:fast", "reports"],
        status: "active",
        createdAt: "2026-08-01 10:00:00",
        lastUsedAt: "2026-08-08 09:30:00",
        ipAllowlist: [],
      }],
    },
  }));
  await page.route("**/api/developer/ledger?**", (route) => route.fulfill({
    json: { status: "success", entries: [] },
  }));
}

async function expectNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth, `页面横向溢出 ${dimensions.scrollWidth - dimensions.clientWidth}px`).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

async function expectHorizontalHeading(page: Page, selector: string) {
  const heading = page.locator(selector);
  await expect(heading).toBeVisible();
  const box = await heading.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(box!.height * 1.5);
  await expect(heading).toHaveCSS("writing-mode", "horizontal-tb");
}

async function expectBoxesDoNotOverlap(page: Page, selector: string) {
  const boxes = await page.locator(selector).evaluateAll((elements) => elements.map((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
  }));
  for (let leftIndex = 0; leftIndex < boxes.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < boxes.length; rightIndex += 1) {
      const left = boxes[leftIndex];
      const right = boxes[rightIndex];
      const overlapWidth = Math.min(left.right, right.right) - Math.max(left.left, right.left);
      const overlapHeight = Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top);
      expect(overlapWidth > 1 && overlapHeight > 1, `元素 ${leftIndex} 与 ${rightIndex} 发生重叠`).toBeFalsy();
    }
  }
}

for (const viewport of viewports) {
  test(`官网首页在 ${viewport.name} 视口保持横向主视觉`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installBaseMocks(page);
    await page.goto("/");

    await expectHorizontalHeading(page, "#official-home-title");
    await expect(page.getByRole("figure", { name: "慧鉴AI品牌助手小鉴与鉴伪工具" })).toBeVisible();
    const mascotLoaded = await page.locator('.home-hero-visual-stage > img').evaluate((image) => {
      const element = image as HTMLImageElement;
      return element.complete && element.naturalWidth > 0;
    });
    expect(mascotLoaded).toBeTruthy();
    if (viewport.name === "mobile") {
      const valueRail = await page.locator(".home-value-rail").boundingBox();
      expect(valueRail).not.toBeNull();
      expect(valueRail!.y).toBeLessThan(viewport.height);
    }
    await expectNoHorizontalOverflow(page);
  });
}

test("移动官网导航支持键盘关闭并恢复焦点", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "打开网站导航" });
  await trigger.click();
  await expect(page.getByRole("navigation", { name: "移动端官网导航" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("navigation", { name: "移动端官网导航" })).toBeHidden();
  await expect(trigger).toBeFocused();
});

for (const viewport of viewports.slice(0, 2)) {
  test(`鉴伪入口在 ${viewport.name} 视口居中且控件不重叠`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installBaseMocks(page);
    await page.goto("/?workspace=1");

    await expectHorizontalHeading(page, ".upload-stage h3");
    await expectBoxesDoNotOverlap(page, ".compact-capability-strip > div");
    const workspace = await page.locator(".agent-workspace").boundingBox();
    const upload = await page.locator(".upload-stage").boundingBox();
    expect(workspace).not.toBeNull();
    expect(upload).not.toBeNull();
    expect(Math.abs((upload!.x + upload!.width / 2) - (workspace!.x + workspace!.width / 2))).toBeLessThanOrEqual(2);

    await page.getByRole("button", { name: "选择图片检测模型" }).click();
    const modelMenu = page.getByRole("listbox", { name: "图片检测模型" });
    await expect(modelMenu).toBeVisible();
    const menuBox = await modelMenu.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(0);
    expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(viewport.width + 1);
    await expectNoHorizontalOverflow(page);
  });
}

test("开发者平台移动布局与 API Key 弹窗满足键盘交互", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/?developer=1");

  await expect(page.getByRole("heading", { name: "把慧鉴AI接入你的业务流程" })).toBeVisible();
  await expect(page.getByRole("button", { name: "API Keys" })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "API Keys" }).click();
  const createButton = page.getByRole("button", { name: "创建 API Key" });
  await createButton.click();
  const dialog = page.getByRole("dialog", { name: "创建 API Key" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "创建 Key" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(createButton).toBeFocused();
});
