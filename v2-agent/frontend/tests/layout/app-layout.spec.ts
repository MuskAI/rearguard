import { expect, Page, test } from "@playwright/test";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

const videoFixture = fileURLToPath(new URL(
  "../../../../realguard-server-main/RealGuard/imagedetection/static/system/video189.mp4",
  import.meta.url,
));

const viewports = [
  { name: "mobile", width: 390, height: 844 },
  { name: "desktop", width: 1440, height: 1000 },
  { name: "short-desktop", width: 1440, height: 800 },
  { name: "safari-wide", width: 1990, height: 1240 },
  { name: "wide", width: 2560, height: 1440 },
  { name: "compact-desktop", width: 1366, height: 600 },
  { name: "compact-mobile", width: 390, height: 600 },
  { name: "mobile-landscape", width: 844, height: 390 },
] as const;

const user = {
  Userid: 7,
  account_uuid: "layout-review-user",
  username: "验收用户",
  phone: "138****0000",
};

async function installBaseMocks(page: Page, authenticated = false, historyFailure = false) {
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
    status: historyFailure ? 503 : 200,
    json: { items: [], total: 0, filterCounts: {} },
  }));
  await page.route("**/api/history/image-detections**", (route) => route.fulfill({
    status: historyFailure ? 503 : 200,
    json: { status: "success", records: [], total: 0 },
  }));
  await page.route("**/api/history/video-detections**", (route) => route.fulfill({
    status: historyFailure ? 503 : 200,
    json: { status: "success", records: [], total: 0 },
  }));
}

async function installDeveloperMocks(page: Page, failures: Partial<Record<"account" | "keys" | "ledger", boolean>> = {}) {
  await page.route("**/api/developer/account?**", (route) => route.fulfill({
    status: failures.account ? 503 : 200,
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
    status: failures.keys ? 503 : 200,
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
    status: failures.ledger ? 503 : 200,
    json: { status: "success", entries: [] },
  }));
}

async function expectNoHorizontalOverflow(page: Page) {
  const containers = await page.evaluate(() => [
    { name: "document", clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth },
    ...Array.from(document.querySelectorAll<HTMLElement>(".official-site"), (element) => ({
      name: ".official-site",
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    })),
  ]);
  for (const dimensions of containers) {
    expect(dimensions.scrollWidth, `${dimensions.name} 横向溢出 ${dimensions.scrollWidth - dimensions.clientWidth}px`).toBeLessThanOrEqual(dimensions.clientWidth + 1);
  }
}

async function expectNoInternalOverflow(page: Page, selectors: string[]) {
  const dimensions = await page.evaluate((targets) => targets.map((selector) => {
    const element = document.querySelector<HTMLElement>(selector);
    return element ? {
      selector,
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    } : null;
  }), selectors);
  for (const item of dimensions) {
    expect(item, `${item?.selector || "目标元素"} 不存在`).not.toBeNull();
    expect(item!.scrollWidth, `${item!.selector} 内容被裁切`).toBeLessThanOrEqual(item!.clientWidth + 1);
  }
}

async function expectTouchTargets(page: Page, selectors: string[]) {
  const dimensions = await page.evaluate((targets) => targets.map((selector) => {
    const element = document.querySelector<HTMLElement>(selector);
    if (!element) return null;
    const bounds = element.getBoundingClientRect();
    return { selector, width: bounds.width, height: bounds.height };
  }), selectors);
  for (const item of dimensions) {
    expect(item, `${item?.selector || "触控元素"} 不存在`).not.toBeNull();
    expect(item!.width, `${item!.selector} 触控宽度不足`).toBeGreaterThanOrEqual(44);
    expect(item!.height, `${item!.selector} 触控高度不足`).toBeGreaterThanOrEqual(44);
  }
}

async function readableTextOffenders(page: Page, rootSelector: string, minimumPx = 12) {
  return page.locator(rootSelector).evaluate((root, minimum) => {
    const selectors = "p,span,small,strong,b,button,a,label,summary,dt,dd,li,h1,h2,h3,h4";
    return Array.from(root.querySelectorAll<HTMLElement>(selectors)).flatMap((element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const ownText = Array.from(element.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent || "")
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
      if (!ownText || style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0 || rect.width < 1 || rect.height < 1) return [];
      const size = Number.parseFloat(style.fontSize);
      return size + 0.01 < minimum ? [{
        tag: element.tagName,
        className: element.className,
        parentClassName: element.parentElement?.className || "",
        ancestry: Array.from({ length: 4 }, (_, index) => {
          let parent: HTMLElement | null = element;
          for (let step = 0; step <= index; step += 1) parent = parent?.parentElement || null;
          return parent ? `${parent.tagName}.${parent.className}` : "";
        }),
        text: ownText.slice(0, 42),
        size,
      }] : [];
    });
  }, minimumPx);
}

async function expectHorizontalHeading(page: Page, selector: string) {
  const heading = page.locator(selector);
  await expect(heading).toBeVisible();
  const box = await heading.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(box!.height * 1.5);
  await expect(heading).toHaveCSS("writing-mode", "horizontal-tb");
}

async function expectReadableHero(page: Page, viewportWidth: number) {
  const metrics = await page.locator(".home-hero").evaluate((hero) => {
    const copy = hero.querySelector<HTMLElement>(".home-hero-copy")!;
    const title = hero.querySelector<HTMLElement>("h1")!;
    const subtitle = hero.querySelector<HTMLElement>("h2")!;
    const copyRect = copy.getBoundingClientRect();
    const titleRect = title.getBoundingClientRect();
    const subtitleRect = subtitle.getBoundingClientRect();
    return {
      copyWidth: copyRect.width,
      titleHeight: titleRect.height,
      titleLineHeight: Number.parseFloat(getComputedStyle(title).lineHeight),
      subtitleHeight: subtitleRect.height,
      subtitleLineHeight: Number.parseFloat(getComputedStyle(subtitle).lineHeight),
    };
  });
  const expectedCopyWidth = Math.min(420, viewportWidth - 36);
  expect(metrics.copyWidth, "主视觉文案列被挤压").toBeGreaterThanOrEqual(expectedCopyWidth - 1);
  expect(metrics.titleHeight, "品牌标题发生多行折叠").toBeLessThanOrEqual(metrics.titleLineHeight * 1.25);
  expect(metrics.subtitleHeight, "主标语超过预期的两行").toBeLessThanOrEqual(metrics.subtitleLineHeight * 2.25);
}

async function expectHeroFirstViewport(page: Page, viewport: { width: number; height: number }) {
  const metrics = await page.evaluate(() => {
    const rect = (selector: string) => {
      const bounds = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
      return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom, width: bounds.width, height: bounds.height };
    };
    return {
      primaryAction: rect(".home-hero-actions button"),
      mascot: rect(".home-hero-visual-stage > img"),
      nextSectionTitle: rect(".home-value-rail article:first-child strong"),
    };
  });
  const visibleRatio = (bounds: typeof metrics.mascot) => {
    const visibleWidth = Math.max(0, Math.min(bounds.right, viewport.width) - Math.max(bounds.left, 0));
    const visibleHeight = Math.max(0, Math.min(bounds.bottom, viewport.height) - Math.max(bounds.top, 0));
    return (visibleWidth * visibleHeight) / Math.max(1, bounds.width * bounds.height);
  };
  expect(visibleRatio(metrics.primaryAction), "主操作未完整进入首屏").toBeGreaterThanOrEqual(0.99);
  expect(visibleRatio(metrics.mascot), "品牌形象在首屏内的可见面积不足 90%").toBeGreaterThanOrEqual(0.9);
  expect(metrics.nextSectionTitle.top, "下一段标题没有进入首屏").toBeLessThan(viewport.height);
  expect(metrics.nextSectionTitle.bottom, "下一段标题在首屏中被截断").toBeLessThanOrEqual(viewport.height);
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

async function expectCapabilityContentDoesNotOverlap(page: Page) {
  const collisions = await page.locator(".compact-capability-strip > div").evaluateAll((items) => items.flatMap((item, index) => {
    const icon = item.querySelector<HTMLElement>(".brand-art-icon")?.getBoundingClientRect();
    const copy = item.querySelector<HTMLElement>(":scope > span:not(.brand-art-icon)")?.getBoundingClientRect();
    if (!icon || !copy) return [`能力项 ${index + 1} 缺少图标或文字`];
    const overlapWidth = Math.min(icon.right, copy.right) - Math.max(icon.left, copy.left);
    const overlapHeight = Math.min(icon.bottom, copy.bottom) - Math.max(icon.top, copy.top);
    return overlapWidth > 1 && overlapHeight > 1 ? [`能力项 ${index + 1} 的图标与文字重叠`] : [];
  }));
  expect(collisions).toEqual([]);
}

for (const viewport of viewports) {
  test(`官网首页在 ${viewport.name} 视口保持横向主视觉`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installBaseMocks(page);
    await page.goto("/");

    await expectHorizontalHeading(page, "#official-home-title");
    await expectReadableHero(page, viewport.width);
    await expectHeroFirstViewport(page, viewport);
    await expect(page.getByRole("figure", { name: "图片、视频与文档经过小鉴核验后形成证据" })).toBeVisible();
    const mascotLoaded = await page.locator('.home-hero-visual-stage > img').evaluate((image) => {
      const element = image as HTMLImageElement;
      return element.complete && element.naturalWidth > 0;
    });
    expect(mascotLoaded).toBeTruthy();
    if (viewport.width <= 700) {
      await expect(page.locator(".home-header .brand-copy small")).toBeHidden();
    }
    await expectNoHorizontalOverflow(page);
  });
}

test("官网主视觉在响应式临界宽度不会收缩成单字列", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await installBaseMocks(page);
  await page.goto("/");

  for (const width of [320, 360, 420, 700, 820, 960, 961, 1080, 1180, 1181, 1440, 1990]) {
    await page.setViewportSize({ width, height: 1000 });
    await expectHorizontalHeading(page, "#official-home-title");
    await expectReadableHero(page, width);
    await expectNoHorizontalOverflow(page);
  }
});

test("登录状态下官网导航不会挤压账号与鉴伪按钮", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await installBaseMocks(page, true);
  await page.goto("/");
  await expect(page.locator(".home-header .account-menu-trigger")).toBeVisible();

  for (const width of [1440, 1366, 1180, 1120, 961]) {
    await page.setViewportSize({ width, height: 800 });
    const layout = await page.evaluate(() => {
      const bounds = (selector: string) => {
        const rect = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
        return { left: rect.left, right: rect.right };
      };
      const label = document.querySelector<HTMLElement>(".home-workspace-button .home-label-wide")!;
      const labelStyle = getComputedStyle(label);
      const nav = document.querySelector<HTMLElement>(".home-desktop-nav")!;
      const menu = document.querySelector<HTMLElement>(".home-mobile-menu-button")!;
      return {
        labelHeight: label.getBoundingClientRect().height,
        labelLineHeight: Number.parseFloat(labelStyle.lineHeight),
        navVisible: getComputedStyle(nav).display !== "none",
        menuVisible: getComputedStyle(menu).display !== "none",
        brand: bounds(".home-brand-link"),
        nav: bounds(".home-desktop-nav"),
        actions: bounds(".home-header-actions"),
      };
    });

    expect(layout.labelHeight, `${width}px 下“开始鉴伪”发生换行`).toBeLessThanOrEqual(layout.labelLineHeight + 1);
    expect(layout.brand.right, `${width}px 下品牌与操作区发生重叠`).toBeLessThanOrEqual(layout.actions.left + 1);
    if (width > 1120) {
      expect(layout.navVisible).toBeTruthy();
      expect(layout.menuVisible).toBeFalsy();
      expect(layout.brand.right, `${width}px 下品牌与桌面导航发生重叠`).toBeLessThanOrEqual(layout.nav.left + 1);
      expect(layout.nav.right, `${width}px 下桌面导航与操作区发生重叠`).toBeLessThanOrEqual(layout.actions.left + 1);
    } else {
      expect(layout.navVisible).toBeFalsy();
      expect(layout.menuVisible).toBeTruthy();
    }
    await expectBoxesDoNotOverlap(page, ".home-header-actions > *");
    await expectNoHorizontalOverflow(page);
  }
});

test("移动官网导航支持键盘关闭并恢复焦点", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "打开网站导航" });
  await trigger.click();
  const navigation = page.getByRole("navigation", { name: "移动端官网导航" });
  await expect(navigation).toBeVisible();
  await expect(navigation.locator("a").first()).toBeFocused();
  await expect(navigation.locator(":scope > *")).toHaveCount(5);
  expect((await navigation.locator(":scope > *").allTextContents()).map((label) => label.trim())).toEqual([
    "产品能力",
    "Playground",
    "开发者平台",
    "常见问题",
    "关于与合作",
  ]);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("navigation", { name: "移动端官网导航" })).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("桌面官网导航只保留核心入口并将关于与合作置于末尾", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await installBaseMocks(page);
  await page.goto("/");

  const navigation = page.getByRole("navigation", { name: "官网导航" });
  await expect(navigation.locator(":scope > *")).toHaveCount(5);
  expect((await navigation.locator(":scope > *").allTextContents()).map((label) => label.trim())).toEqual([
    "产品能力",
    "Playground",
    "开发者平台",
    "常见问题",
    "关于与合作",
  ]);
});

test("官网直接展示 Agent Skill 并可复制一句话进入完整接入页", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/");

  const section = page.locator(".home-agent-skill");
  await section.scrollIntoViewIfNeeded();
  await expect(page.getByRole("heading", { name: "把鉴伪能力，交给你正在使用的 Agent。" })).toBeVisible();
  await expect(page.getByRole("button", { name: "复制首页 Agent Skill 安装指令" })).toBeVisible();
  const logos = section.locator(".home-agent-skill-logos img");
  await expect(logos).toHaveCount(5);
  expect(await logos.evaluateAll((images: HTMLImageElement[]) => images.every((image) => image.complete && image.naturalWidth > 0))).toBeTruthy();

  await page.getByRole("button", { name: "复制首页 Agent Skill 安装指令" }).click();
  await expect(page.getByRole("button", { name: "复制首页 Agent Skill 安装指令" })).toContainText("已复制");
  await page.getByRole("button", { name: "查看完整接入" }).click();
  await expect(page).toHaveURL(/developerTab=skill/);
  await expect(page.getByRole("heading", { name: "一句话，让你的 Agent 学会鉴伪" })).toBeVisible();
});

test("官网 Agent Skill 在手机端保持完整传递链与可用触控区", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  await page.goto("/");

  const section = page.locator(".home-agent-skill");
  await section.scrollIntoViewIfNeeded();
  await expect(section).toBeVisible();
  await expect(section.locator(".home-agent-skill-logos > span")).toHaveCount(5);
  await expectTouchTargets(page, [
    ".home-agent-skill-actions button",
    ".home-agent-skill-actions a",
    ".home-agent-skill-prompt button",
  ]);
  expect(await readableTextOffenders(page, ".home-agent-skill")).toEqual([]);
  await expectNoHorizontalOverflow(page);
});

test("桌面端开发者菜单支持方向键并恢复焦点", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installBaseMocks(page);
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "开发者平台", exact: true });
  await trigger.focus();
  await page.keyboard.press("ArrowDown");
  const menu = page.getByRole("menu", { name: "开发者平台入口" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: /平台概览/ })).toBeFocused();
  await page.keyboard.press("End");
  await expect(menu.getByRole("menuitem", { name: /接入文档/ })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("鼠标从开发者入口移向菜单末项时不会穿过空隙关闭", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installBaseMocks(page);
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "开发者平台", exact: true });
  await trigger.hover();
  const menu = page.getByRole("menu", { name: "开发者平台入口" });
  await expect(menu).toBeVisible();

  const triggerBox = await trigger.boundingBox();
  const menuBox = await menu.boundingBox();
  expect(triggerBox).not.toBeNull();
  expect(menuBox).not.toBeNull();
  const bridgeY = triggerBox!.y + triggerBox!.height + Math.max(1, (menuBox!.y - triggerBox!.y - triggerBox!.height) / 2);
  await page.mouse.move(triggerBox!.x + triggerBox!.width / 2, bridgeY, { steps: 4 });
  await page.waitForTimeout(80);
  await expect(trigger).toHaveAttribute("aria-expanded", "true");

  const lastItem = menu.getByRole("menuitem", { name: /接入文档/ });
  const lastItemBox = await lastItem.boundingBox();
  expect(lastItemBox).not.toBeNull();
  await page.mouse.move(lastItemBox!.x + lastItemBox!.width / 2, lastItemBox!.y + lastItemBox!.height / 2, { steps: 10 });
  await page.waitForTimeout(260);
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(lastItem).toBeVisible();

  await page.mouse.move(12, 180, { steps: 6 });
  await expect(trigger).toHaveAttribute("aria-expanded", "false", { timeout: 800 });
  await expect(menu).toBeHidden();
});

test("官网与统一鉴伪入口不存在低于 12px 的可见文字", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/");
  await expect(page.locator(".home-v3")).toBeVisible();
  expect(await readableTextOffenders(page, ".home-v3")).toEqual([]);

  await page.goto("/?workspace=1");
  await expect(page.locator(".agent-app")).toBeVisible();
  expect(await readableTextOffenders(page, ".agent-app")).toEqual([]);
});

test("核心交互动效短促稳定且从触发位置展开", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/");

  const navLink = page.locator(".home-desktop-nav").getByRole("link", { name: "产品能力" });
  await navLink.hover();
  await expect.poll(() => navLink.evaluate((element) => getComputedStyle(element).transform)).toBe("none");
  const heroMotion = await page.locator(".home-hero-visual-stage").evaluate((stage) => ({
    imageAnimation: getComputedStyle(stage.querySelector("img")!).animationName,
    tokenAnimation: getComputedStyle(stage.querySelector(".hero-art-token")!).animationName,
  }));
  expect(heroMotion).toEqual({ imageAnimation: "none", tokenAnimation: "none" });

  await page.goto("/?workspace=1");
  const uploadStage = page.locator(".upload-stage");
  const uploadMotion = await uploadStage.evaluate((element) => ({
    transform: getComputedStyle(element).transform,
    cornerBefore: getComputedStyle(element, "::before").display,
    cornerAfter: getComputedStyle(element, "::after").display,
  }));
  expect(uploadMotion).toEqual({ transform: "none", cornerBefore: "none", cornerAfter: "none" });

  await page.getByRole("button", { name: "选择图片检测模型" }).click();
  const menuMotion = await page.locator(".analysis-model-menu").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      origin: style.transformOrigin,
      durations: style.transitionDuration.split(",").map((value) => Number.parseFloat(value) * 1000),
      timing: style.transitionTimingFunction,
    };
  });
  expect(menuMotion.origin).toMatch(/^0px 0px/);
  expect(Math.max(...menuMotion.durations)).toBeLessThanOrEqual(180);
  expect(menuMotion.timing).toContain("cubic-bezier(0.23, 1, 0.32, 1)");
});

test("官网可以进入独立 Playground", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Playground" }).click();
  await expect(page).toHaveURL(/playground=1/);
  await expect(page.getByRole("heading", { name: "找出那张 AI 图" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "慧鉴AI" })).toBeVisible();
});

for (const viewport of [
  { name: "about-mobile", width: 390, height: 844 },
  { name: "about-desktop", width: 1440, height: 1000 },
  { name: "about-landscape", width: 844, height: 390 },
]) {
  test(`关于与合作页面在 ${viewport.name} 视口完整可读`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installBaseMocks(page);
    await page.goto("/?about=1");

    await expectHorizontalHeading(page, "#about-title");
    await expect(page.getByRole("heading", { name: /让真假判断/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "我们希望和认真解决问题的人，一起往前走。" })).toBeVisible();
    const portraitLoaded = await page.locator(".about-portrait-stage > img").evaluate((image) => {
      const element = image as HTMLImageElement;
      return element.complete && element.naturalWidth === 256 && element.naturalHeight === 256;
    });
    expect(portraitLoaded).toBeTruthy();
    expect(await readableTextOffenders(page, ".about-site")).toEqual([]);
    await expectNoHorizontalOverflow(page);
  });
}

test("官网导航可以进入关于与合作页面并返回产品首页", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/");

  await page.locator(".home-desktop-nav").getByRole("link", { name: "关于与合作" }).click();
  await expect(page).toHaveURL(/about=1/);
  await expect(page.locator("#about-title")).toBeVisible();
  await page.locator(".about-header .home-brand-link").click();
  await expect(page.locator("#official-home-title")).toBeVisible();
});

test("开放合作表单提交后展示可追踪的成功状态", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  let submitted: Record<string, unknown> | null = null;
  await page.route(/\/v2-api\/collaboration-inquiries(?:\?|$)/, async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 201,
      json: { status: "accepted", inquiryId: "coop-layout0001", createdAt: "2026-08-11T12:00:00Z" },
    });
  });
  await page.goto("/?about=1#cooperation");

  await page.getByLabel("怎么称呼你").fill("评测伙伴");
  await page.getByLabel(/所在机构/).fill("真实性研究中心");
  await page.getByLabel("联系方式").fill("partner@example.com");
  await page.getByLabel("你想一起解决什么问题").fill("希望使用授权数据共同评估模型在跨平台压缩场景中的真实表现与误判情况。");
  await page.getByLabel(/我同意仅为合作沟通/).check();
  await page.getByRole("button", { name: "提交合作意向" }).click();

  await expect(page.getByText("合作意向已提交")).toBeVisible();
  await expect(page.getByText("参考编号：coop-layout0001")).toBeVisible();
  expect(submitted).toMatchObject({
    collaborationType: "research",
    name: "评测伙伴",
    organization: "真实性研究中心",
    contact: "partner@example.com",
    privacyAccepted: true,
  });
});

test("Playground 保持单一六选一玩法并自动进入下一轮", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/?playground=1");

  await expect(page.getByRole("heading", { name: "找出那张 AI 图" })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始游戏" })).toHaveCount(1);
  await expect(page.getByText("游戏大厅")).toHaveCount(0);
  await page.getByRole("button", { name: "开始游戏" }).click();

  const choices = page.getByRole("button", { name: /选择图片/ });
  await expect(choices).toHaveCount(6);
  expect(await page.locator(".simple-card img").evaluateAll((images) => images.map((image) => image.getAttribute("alt")))).toEqual([
    "候选图片 1",
    "候选图片 2",
    "候选图片 3",
    "候选图片 4",
    "候选图片 5",
    "候选图片 6",
  ]);
  expect(await page.locator(".simple-card img").evaluateAll((images) => images.every((image) => /\/playground\/samples\/sample-\d+\.webp$/.test((image as HTMLImageElement).src)))).toBeTruthy();

  await choices.nth(0).click();
  await expect(choices.nth(0)).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".simple-feedback")).toBeVisible();
  await expect(page.locator(".card-answer.is-ai")).toHaveCount(1);
  await expect(page.locator(".card-answer.is-real")).toHaveCount(5);

  await expect(page.locator(".round-count strong")).toHaveText("02", { timeout: 3_000 });
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("2");
  await expect(page.getByRole("button", { name: "选择图片 2" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".simple-feedback")).toBeVisible();
  await expect(page.getByText("这是一个观察小游戏，不是鉴伪结论。")).toBeVisible();
  expect(await readableTextOffenders(page, ".playground-page")).toEqual([]);
  await expectNoHorizontalOverflow(page);
});

test("Playground 完成八轮后记录本机 Top 10 榜单", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/?playground=1");
  await page.getByRole("button", { name: "开始游戏" }).click();

  const aiSamples = new Set(["sample-02.webp", "sample-05.webp", "sample-09.webp", "sample-13.webp", "sample-16.webp"]);
  for (let round = 0; round < 8; round += 1) {
    const cards = page.locator(".simple-card");
    await expect(cards).toHaveCount(6);
    const sources = await cards.locator("img").evaluateAll((images) => images.map((image) => (image as HTMLImageElement).src.split("/").pop() || ""));
    const answerIndex = sources.findIndex((source) => aiSamples.has(source));
    expect(answerIndex).toBeGreaterThanOrEqual(0);
    await cards.nth(answerIndex).click();
    await expect(page.locator(".simple-feedback.is-correct")).toBeVisible();
    if (round < 7) await expect(page.locator(".round-count strong")).toHaveText(String(round + 2).padStart(2, "0"), { timeout: 3_000 });
  }

  await expect(page.getByRole("heading", { name: "你的分数" })).toBeVisible({ timeout: 3_000 });
  await expect(page.locator(".final-score")).toHaveText("1500");
  await expect(page.getByText("找对了 8 / 8 张", { exact: false })).toBeVisible();
  await page.getByRole("textbox", { name: "输入你的名字" }).fill("测试玩家");
  await page.getByRole("button", { name: "登上榜单" }).click();
  await expect(page.locator(".rank-result")).toContainText("第 1 名");
  await expect(page.locator(".leaderboard-panel li")).toHaveCount(1);
  await expect(page.locator(".leaderboard-panel li")).toContainText("测试玩家");
});

test("Playground 手机端保持双列布局并正确扣减机会", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  await page.goto("/?playground=1");
  await page.getByRole("button", { name: "开始游戏" }).click();

  const cards = page.locator(".simple-card");
  const positions = await cards.evaluateAll((items) => items.slice(0, 2).map((item) => {
    const rect = item.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width };
  }));
  expect(positions[1].left).toBeGreaterThan(positions[0].left + positions[0].width - 1);
  expect(Math.abs(positions[1].top - positions[0].top)).toBeLessThanOrEqual(1);

  const aiSamples = new Set(["sample-02.webp", "sample-05.webp", "sample-09.webp", "sample-13.webp", "sample-16.webp"]);
  const sources = await cards.locator("img").evaluateAll((images) => images.map((image) => (image as HTMLImageElement).src.split("/").pop() || ""));
  const realIndex = sources.findIndex((source) => !aiSamples.has(source));
  await cards.nth(realIndex).click();
  await expect(page.locator(".life-stat")).toHaveAttribute("aria-label", "剩余 2 次机会");
  await expect(page.locator(".simple-feedback.is-wrong")).toBeVisible();
  expect(await readableTextOffenders(page, ".playground-page")).toEqual([]);
  await expectNoHorizontalOverflow(page);
});

test("移动端一级页面拥有独立滚动容器并可到达末尾操作", async ({ page }) => {
  await installBaseMocks(page);
  const cases = [
    { url: "/?playground=1", root: ".playground-page", last: ".simple-hint" },
    { url: "/?about=1", root: ".about-site", last: ".about-footer" },
  ];
  const viewports = [
    { width: 320, height: 568 },
    { width: 844, height: 390 },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const item of cases) {
      await page.goto(item.url);
      const root = page.locator(item.root);
      await expect(root).toBeVisible();
      const before = await root.evaluate((element) => ({
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        overflowY: getComputedStyle(element).overflowY,
      }));
      expect(before.overflowY).toBe("auto");
      expect(before.scrollHeight).toBeGreaterThan(before.clientHeight);
      await root.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
      await expect.poll(() => root.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
      await expect(page.locator(item.last)).toBeVisible();
      await expectNoHorizontalOverflow(page);
    }
  }
});

test("Playground 紧凑手机顶栏保留可识别登录入口", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await installBaseMocks(page);
  await page.goto("/?playground=1");

  const login = page.getByRole("button", { name: "登录账号" });
  await expect(login).toBeVisible();
  await expect(login).toContainText("登录");
  await expect(page.locator(".playground-header .brand-copy small")).toBeHidden();
  await expectTouchTargets(page, [".playground-login", ".playground-workspace"]);
  await expectNoInternalOverflow(page, [".playground-header", ".playground-header-actions"]);
  await expectNoHorizontalOverflow(page);
});

test("深色工作方式区块的文字、图标与按钮保持可读对比度", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.goto("/");

  const workflow = page.locator(".home-workflow");
  await workflow.scrollIntoViewIfNeeded();
  await expect(workflow).toBeVisible();
  const results = await workflow.evaluate((section) => {
    const parseRgb = (value: string) => (value.match(/[\d.]+/g) || [])
      .slice(0, 3)
      .map((channel) => Number(channel) / 255);
    const luminance = (value: string) => {
      const [red, green, blue] = parseRgb(value).map((channel) => (
        channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
      ));
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    };
    const ratio = (foreground: string, background: string) => {
      const lighter = Math.max(luminance(foreground), luminance(background));
      const darker = Math.min(luminance(foreground), luminance(background));
      return (lighter + 0.05) / (darker + 0.05);
    };
    const sectionBackground = getComputedStyle(section).backgroundColor;
    const samples = [
      ["eyebrow", ".home-workflow-heading > p", sectionBackground, 4.5],
      ["heading", ".home-workflow-heading h2", sectionBackground, 4.5],
      ["step number", ".home-workflow li > span", sectionBackground, 4.5],
      ["step title", ".home-workflow li strong", sectionBackground, 4.5],
      ["step description", ".home-workflow li p", sectionBackground, 4.5],
      ["step icon", ".home-workflow .brand-art-icon", sectionBackground, 3],
    ] as const;
    const measured = samples.map(([name, selector, background, minimum]) => {
      const element = section.querySelector<HTMLElement>(selector)!;
      const foreground = getComputedStyle(element).color;
      return { name, foreground, background, minimum, ratio: ratio(foreground, background) };
    });
    const button = section.querySelector<HTMLElement>(".home-workflow-heading button")!;
    const buttonStyle = getComputedStyle(button);
    measured.push({
      name: "workflow button",
      foreground: buttonStyle.color,
      background: buttonStyle.backgroundColor,
      minimum: 4.5,
      ratio: ratio(buttonStyle.color, buttonStyle.backgroundColor),
    });
    return measured;
  });

  for (const result of results) {
    expect(result.ratio, `${result.name} 对比度仅 ${result.ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(result.minimum);
  }
});

test("PDF 文档会展示逐图检测结果并支持放大复核", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  const preview = `data:image/svg+xml;base64,${Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#e8eef9"/><rect x="390" y="190" width="170" height="90" fill="#315fa9"/><text x="40" y="80" font-size="36" fill="#17212b">Evidence image</text></svg>',
  ).toString("base64")}`;
  await page.route(/\/v2-api\/document-detections(?:\?|$)/, (route) => route.fulfill({
    status: 202,
    json: {
      id: "doc_layout_test",
      filename: "evidence.pdf",
      mime: "application/pdf",
      size: 2048,
      sha256: "a".repeat(64),
      mode: "fast",
      status: "completed",
      stage: "completed",
      progress: 100,
      pageCount: 2,
      discovered: 1,
      completed: 1,
      succeeded: 1,
      failed: 0,
      warnings: [],
      assets: [{
        ordinal: 1,
        pageNumber: 2,
        occurrenceIndex: 1,
        sourceKind: "pdf_page_image",
        mime: "image/png",
        width: 640,
        height: 360,
        sha256: "b".repeat(64),
        status: "completed",
        preview,
        verdict: "highly_suspected_fake",
        verdictLabel: "AI 生成图像",
        aiProbability: 0.91,
        confidence: 0.91,
        modelVersion: "DINOv3",
        source: "primary_model",
        explanation: "主模型与来源证据完成交叉核验。",
        regions: [{ x: 0.61, y: 0.53, w: 0.27, h: 0.25, label: "可疑区域", score: 0.9 }],
      }],
      assetOffset: 0,
      assetLimit: 24,
      assetTotal: 1,
      hasMoreAssets: false,
      summary: {
        verdict: "highly_suspected_fake",
        verdictLabel: "发现 AI 生成图像",
        realCount: 0,
        fakeCount: 1,
        averageAiProbability: 0.91,
      },
      error: null,
      createdAt: "2026-08-08T00:00:00Z",
      updatedAt: "2026-08-08T00:00:01Z",
      accessToken: "layout-document-token",
    },
  }));

  await page.goto("/?workspace=1");
  await page.getByRole("checkbox", { name: /我授权平台处理本次上传文件/ }).check();
  await page.locator('input[type="file"]').setInputFiles({
    name: "evidence.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7 layout fixture"),
  });

  await expect(page.getByRole("heading", { name: "发现 AI 生成图像" })).toBeVisible();
  await expect(page.getByText("AI 风险 91% · 640×360")).toBeVisible();
  await page.getByRole("button", { name: "放大查看第 2 页" }).click();
  const dialog = page.getByRole("dialog", { name: "第 2 页放大图" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭放大视图" })).toBeFocused();
  await expect(page.getByText("主模型与来源证据完成交叉核验。")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "放大查看第 2 页" })).toBeFocused();
  await expectNoHorizontalOverflow(page);
});

test("Router Lab 在手机端展示逐图依据并保持可滚动", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  const gray = `data:image/svg+xml;base64,${Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><rect width="320" height="220" fill="#999"/></svg>',
  ).toString("base64")}`;
  const photo = `data:image/svg+xml;base64,${Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="380" height="260"><rect width="380" height="260" fill="#dfe9f7"/><circle cx="190" cy="130" r="88" fill="#2869df"/></svg>',
  ).toString("base64")}`;
  await page.route(/\/v2-api\/document-router\/preview(?:\?|$)/, (route) => route.fulfill({
    json: {
      filename: "router.pdf",
      mime: "application/pdf",
      size: 327680,
      sha256: "a".repeat(64),
      pageCount: 2,
      warnings: [],
      routerVersion: "document-router-rules-v1",
      elapsedMs: 188,
      summary: {
        extracted: 3,
        detect: 1,
        skip: 1,
        uncertain: 1,
        recommendedModelCalls: 2,
        modelCallsAvoided: 1,
      },
      assets: [
        {
          ordinal: 1,
          pageNumber: 1,
          occurrenceIndex: 1,
          sourceKind: "pdf_embedded",
          mime: "image/png",
          width: 320,
          height: 220,
          sha256: "b".repeat(64),
          preview: gray,
          router: {
            route: "skip",
            shouldDetect: false,
            confidence: 0.997,
            category: "uniform_layer",
            categoryLabel: "纯色或低信息图层",
            reasons: ["主色占比为 100.0%", "更接近背景、蒙版或占位块"],
            features: { entropy: 0, dominantColorRatio: 1, width: 320, height: 220 },
            version: "document-router-rules-v1",
          },
        },
        {
          ordinal: 2,
          pageNumber: 1,
          occurrenceIndex: 2,
          sourceKind: "pdf_embedded",
          mime: "image/png",
          width: 380,
          height: 260,
          sha256: "c".repeat(64),
          preview: photo,
          router: {
            route: "detect",
            shouldDetect: true,
            confidence: 0.9,
            category: "photo_or_artwork",
            categoryLabel: "照片或完整视觉作品",
            reasons: ["具备可分析细节", "纹理、边缘和色彩分布符合完整视觉内容"],
            features: { entropy: 4.8, dominantColorRatio: 0.3, width: 380, height: 260 },
            version: "document-router-rules-v1",
          },
        },
        {
          ordinal: 3,
          pageNumber: 2,
          occurrenceIndex: 1,
          sourceKind: "pdf_embedded",
          mime: "image/png",
          width: 260,
          height: 180,
          sha256: "d".repeat(64),
          preview: gray,
          router: {
            route: "uncertain",
            shouldDetect: true,
            confidence: 0.66,
            category: "ambiguous_visual",
            categoryLabel: "边界视觉内容",
            reasons: ["当前规则不足以确认类型", "正式流程默认继续进入快速检测"],
            features: { entropy: 2.4, dominantColorRatio: 0.8, width: 260, height: 180 },
            version: "document-router-rules-v1",
          },
        },
      ],
    },
  }));

  await page.goto("/?router=1");
  await page
    .getByRole("region", { name: "上传测试文档" })
    .locator('input[type="file"]')
    .setInputFiles({
    name: "router.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-router-layout"),
  });
  await page.getByRole("button", { name: "开始 Router 测试" }).click();

  await expect(page.getByText("减少 1 次模型调用")).toBeVisible();
  await expect(page.getByRole("heading", { name: "纯色或低信息图层" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "照片或完整视觉作品" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "边界视觉内容" })).toBeVisible();
  const geometry = await page.locator(".router-lab-page").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(geometry.scrollWidth).toBe(geometry.clientWidth);
  expect(geometry.scrollHeight).toBeGreaterThan(geometry.clientHeight);
});

test("刷新工作台会恢复文档结果而不会重新上传", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    window.sessionStorage.setItem("huijian-active-document-task", JSON.stringify({
      id: "doc_resume_test",
      accessToken: "resume-token",
      owner: "guest",
    }));
  });
  await installBaseMocks(page);
  let queryCount = 0;
  await page.route(/\/v2-api\/document-detections\/doc_resume_test(?:\?|$)/, (route) => {
    queryCount += 1;
    return route.fulfill({
      json: {
        id: "doc_resume_test",
        filename: "resume.docx",
        mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size: 4096,
        sha256: "c".repeat(64),
        mode: "fast",
        status: "completed",
        stage: "completed",
        progress: 100,
        pageCount: null,
        discovered: 1,
        completed: 1,
        succeeded: 1,
        failed: 0,
        warnings: [],
        assets: [],
        assetOffset: 0,
        assetLimit: 100,
        assetTotal: 0,
        hasMoreAssets: false,
        summary: {
          verdict: "real",
          verdictLabel: "未发现 AI 生成图像",
          realCount: 1,
          fakeCount: 0,
          averageAiProbability: 0.08,
        },
        error: null,
        createdAt: "2026-08-08T00:00:00Z",
        updatedAt: "2026-08-08T00:00:01Z",
      },
    });
  });

  await page.goto("/?workspace=1");
  await expect(page.getByRole("heading", { name: "未发现 AI 生成图像" })).toBeVisible();
  await expect(page.locator(".user-file-message strong", { hasText: "resume.docx" })).toBeVisible();
  expect(queryCount).toBe(1);
});

test("结果页以证据链清晰组织结论并保持正文级字号", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page);
  await page.route("**/image_upload/detect_async", (route) => route.fulfill({
    json: {
      status: "success",
      job: {
        id: "layout-result-job",
        version: "1",
        status: "success",
        result: {
          status: "success",
          result: {
            itemid: 901,
            final_label: "AI生成图像",
            probability: 0.87,
            detector_probability: 0.84,
            confidence: "高",
            decisionStatus: "verdict",
            decisionAuthority: "calibrated_model",
            reviewRequired: false,
            modelDecisionReady: true,
            explanation: "真实性分析与证据链已经完成。",
            image_url: "/brand/huijian-forensic-scanner-v3.webp",
            filename: "typography-review.png",
            file_size: "1 KB",
            resolution: "512×512",
            img_format: "PNG",
            visual_issues: ["局部纹理连续性异常"],
            all_metadata: { Software: "Layout review" },
            llm_used: true,
            visibleWatermark: {
              enabled: true,
              supported: true,
              detected: false,
              provider: null,
              confidence: 0,
              evidenceLevel: "none",
              hits: [],
              temporal: { sampledFrames: 1, positiveFrames: 0, moving: false },
              note: "未检出显式 AI 水印。",
            },
          },
        },
      },
    },
  }));
  await page.goto("/?workspace=1");
  await page.locator(".guest-upload-consent input").check();
  await page.locator('input[type="file"]').setInputFiles({
    name: "typography-review.png",
    mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
  });

  await expect(page.locator("#detection-result-title")).toBeVisible();
  const chain = page.locator(".evidence-chain-band");
  await expect(chain).toBeVisible();
  await expect(chain.getByRole("heading", { name: "结论证据链" })).toBeVisible();
  await expect(chain.getByText("2 项关键证据")).toBeVisible();
  await expect(chain.locator(".evidence-chain-item")).toHaveCount(3);
  await expect(chain.locator('.evidence-chain-item[data-impact="fake"]')).toHaveCount(2);
  await expect(chain.getByRole("heading", { name: "真实性分析" })).toBeVisible();
  await expect(chain.getByRole("heading", { name: "视觉复核" })).toBeVisible();
  await expect(chain.getByRole("heading", { name: "实拍来源证据" })).toHaveCount(0);
  await expect(chain.locator(".evidence-chain-legend")).toHaveCount(0);
  await expect(chain.locator(".evidence-chain-final")).toContainText("AI生成图像");
  expect(await chain.locator(".evidence-chain-item").evaluateAll((items) => (
    items.every((item) => getComputedStyle(item).animationName === "none" && getComputedStyle(item).opacity === "1")
  ))).toBeTruthy();
  const sizes = await chain.locator(".evidence-chain-item").first().evaluate((item) => ({
    title: Number.parseFloat(getComputedStyle(item.querySelector("h4")!).fontSize),
    body: Number.parseFloat(getComputedStyle(item.querySelector("p")!).fontSize),
    impact: Number.parseFloat(getComputedStyle(item.querySelector(".evidence-chain-impact")!).fontSize),
  }));
  expect(sizes.title).toBeGreaterThanOrEqual(15);
  expect(sizes.body).toBeGreaterThanOrEqual(14);
  expect(sizes.impact).toBeGreaterThanOrEqual(12);
  expect(await readableTextOffenders(page, ".agent-result")).toEqual([]);

  await page.setViewportSize({ width: 320, height: 568 });
  await expectNoInternalOverflow(page, [
    ".agent-topbar",
    ".topbar-title",
    ".analysis-model-picker",
    ".analysis-model-trigger",
    ".topbar-actions",
  ]);
  await expectTouchTargets(page, [
    ".result-tabs button:nth-child(1)",
    ".result-tabs button:nth-child(2)",
    ".result-tabs button:nth-child(3)",
    ".report-qa-attach",
    ".report-qa-send",
  ]);
  const mobileResult = await page.evaluate(() => {
    const result = document.querySelector<HTMLElement>(".agent-result")!.getBoundingClientRect();
    const preview = document.querySelector<HTMLElement>(".result-preview")!.getBoundingClientRect();
    const dock = document.querySelector<HTMLElement>(".composer-dock")!.getBoundingClientRect();
    return {
      resultLeft: result.left,
      resultRight: result.right,
      previewWidth: preview.width,
      dockBottom: dock.bottom,
    };
  });
  expect(mobileResult.resultLeft).toBeGreaterThanOrEqual(12);
  expect(mobileResult.resultRight).toBeLessThanOrEqual(308);
  expect(mobileResult.previewWidth).toBeLessThanOrEqual(96.5);
  expect(mobileResult.dockBottom).toBeLessThanOrEqual(569);
  await expectNoHorizontalOverflow(page);
});

test("视频结果可预览并按采样时间点回看证据", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await installBaseMocks(page);
  let releaseDetection!: () => void;
  const detectionGate = new Promise<void>((resolve) => { releaseDetection = resolve; });
  await page.route("**/video_upload/detect", async (route) => {
    await detectionGate;
    await route.fulfill({
    json: {
      status: "success",
      result: {
        itemid: 963,
        filename: "video189.mp4",
        video_url: "/api/media/video/963",
        fake_percentage: null,
        real_percentage: null,
        final_label: "AI生成视频",
        confidence: "低",
        decisionStatus: "review_only",
        decisionAuthority: "none",
        reviewRequired: true,
        explanation: "三帧联合时序分析给出 AI 生成方向。",
        frame_count: 3,
        encoder: "video-analysis",
        evidence: {
          schemaVersion: "video-evidence-v1",
          method: "three_frame_temporal_joint",
          sampledFrames: [
            { index: 1, timestamp: 0.5, label: "联合输入帧 1", role: "temporal_model_input" },
            { index: 2, timestamp: 1.0, label: "联合输入帧 2", role: "temporal_model_input" },
            { index: 3, timestamp: 1.5, label: "联合输入帧 3", role: "temporal_model_input" },
          ],
          sampleWindow: { start: 0.5, end: 1.5, duration: 10 },
          keyEvidence: [
            { kind: "model", label: "时序模型方向", detail: "三帧联合输出方向为 AI 生成视频。" },
            { kind: "sampling", label: "实际分析画面", detail: "模型联合读取三个采样时间点。" },
            { kind: "file", label: "视频读取状态", detail: "文件已成功解码。" },
          ],
          limitations: ["当前模型不提供可验证的逐帧真假概率。"],
          processingMs: 2130,
          technical: { fps: 25, totalFrames: 250, codec: "h264" },
        },
        meta: {
          file_size: "2.1 MB",
          duration: 10,
          resolution: "320x240",
          video_format: "MP4",
          fps: 25,
          total_frames: 250,
          codec: "h264",
        },
      },
    },
    });
  });

  await page.goto("/?workspace=1");
  await page.locator(".guest-upload-consent input").check();
  await page.locator('input[type="file"]').setInputFiles(videoFixture);

  const pendingVideo = page.getByLabel("预览待检测视频 video189.mp4");
  await expect(pendingVideo).toBeVisible();
  await expect.poll(() => pendingVideo.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThan(0);
  releaseDetection();

  await expect(page.getByRole("heading", { name: "AI生成视频" })).toBeVisible();
  await expect(page.locator(".agent-topbar")).not.toContainText("video189.mp4");
  await expect(page.locator(".workspace-developer-button")).toHaveCount(0);
  const video = page.getByLabel("预览视频 video189.mp4", { exact: true });
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThan(0);
  const desktopPreview = await page.locator(".agent-result.is-video .result-preview").boundingBox();
  expect(desktopPreview?.width || 0).toBeGreaterThanOrEqual(360);
  expect(desktopPreview?.height || 0).toBeGreaterThanOrEqual(200);

  const expandVideo = page.getByRole("button", { name: "放大预览视频 video189.mp4" });
  await expandVideo.click();
  const dialog = page.getByRole("dialog", { name: "放大预览视频 video189.mp4" });
  await expect(dialog).toBeVisible();
  const largeVideo = page.getByLabel("大画面预览 video189.mp4");
  await expect(largeVideo).toBeVisible();
  await expect.poll(() => largeVideo.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThan(0);
  await page.locator(".video-lightbox-close").click();
  await expect(dialog).toBeHidden();
  await expect(expandVideo).toBeFocused();

  await page.setViewportSize({ width: 390, height: 844 });
  const mobilePreview = await page.locator(".agent-result.is-video .result-preview").boundingBox();
  expect(mobilePreview?.width || 0).toBeGreaterThanOrEqual(300);
  await expect(page.getByRole("heading", { name: "视频采样与时序证据" })).toBeVisible();
  await expect(page.getByText("时序模型方向", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "跳转到采样帧 2，00:01.0" }).click();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(0.8);
  await expect(page.locator(".video-preview-error")).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  await expectTouchTargets(page, [
    '.video-sample-timeline button[aria-label*="采样帧 1"]',
    '.video-sample-timeline button[aria-label*="采样帧 2"]',
    '.video-sample-timeline button[aria-label*="采样帧 3"]',
  ]);
});

test("历史视频通过受保护媒体地址直接预览", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await installBaseMocks(page, true);
  await page.unroute("**/api/history/video-detections**");
  await page.route("**/api/history/video-detections**", (route) => route.fulfill({
    json: {
      status: "success",
      total: 1,
      records: [{
        itemid: 963,
        filename: "history-video.mp4",
        video_url: "/api/media/video/963",
        final_label: "真实视频",
        confidence: "低",
        decision_status: "review_only",
        review_required: true,
        createtime: "2026-08-28 12:00:00",
      }],
    },
  }));
  await page.route("**/video_upload/result?itemid=963", (route) => route.fulfill({
    json: {
      status: "success",
      result: {
        itemid: 963,
        filename: "history-video.mp4",
        video_url: "/api/media/video/963",
        fake_percentage: null,
        real_percentage: null,
        final_label: "真实视频",
        confidence: "低",
        decisionStatus: "review_only",
        decisionAuthority: "none",
        reviewRequired: true,
        explanation: "历史视频已经读取，不重新检测。",
        frame_count: 3,
        encoder: "video-analysis",
        evidence: { sampledFrames: [], keyEvidence: [], limitations: [] },
        meta: { video_format: "MP4", codec: "h264", duration: 10 },
      },
    },
  }));
  let mediaRequests = 0;
  await page.route("**/api/media/video/963", async (route) => {
    mediaRequests += 1;
    await route.fulfill({ path: videoFixture, contentType: "video/mp4" });
  });

  await page.goto("/?workspace=1");
  await page.locator(".history-entry").filter({ hasText: "history-video.mp4" }).click();

  const video = page.getByLabel("预览视频 history-video.mp4", { exact: true });
  await expect(video).toBeVisible();
  await expect(video).toHaveAttribute("src", "/api/media/video/963");
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThan(0);
  expect(mediaRequests).toBeGreaterThan(0);
  await expect(page.getByText("正在打开历史记录")).toHaveCount(0);
});

test("登录用户可以围绕当前检测报告连续提问", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page, true);
  await page.route("**/image_upload/detect_async", (route) => route.fulfill({
    json: {
      status: "success",
      job: {
        id: "qa-result-job",
        version: "1",
        status: "success",
        result: {
          status: "success",
          result: {
            itemid: 902,
            final_label: "AI生成图像",
            probability: 0.91,
            detector_probability: 0.88,
            confidence: "高",
            decisionStatus: "verdict",
            decisionAuthority: "calibrated_model",
            reviewRequired: false,
            modelDecisionReady: true,
            explanation: "可见平台水印与生成痕迹互相印证。",
            image_url: "/brand/huijian-forensic-scanner-v3.webp",
            filename: "qa-review.png",
            file_size: "1 KB",
            resolution: "512×512",
            img_format: "PNG",
            visual_issues: ["右下角存在平台标记"],
            all_metadata: { GPS: "should-not-leave-browser" },
            visibleWatermark: {
              enabled: true,
              supported: true,
              detected: true,
              provider: "示例平台",
              confidence: 0.95,
              evidenceLevel: "strong",
              hits: [{
                provider: "示例平台",
                label: "平台水印",
                confidence: 0.95,
                bbox: { x: 0.78, y: 0.88, w: 0.16, h: 0.08 },
                method: "registry",
                frame: null,
                scores: {},
                decisive: true,
                crop: "data:image/png;base64,should-not-leave-browser",
              }],
              temporal: { sampledFrames: 1, positiveFrames: 1, moving: false },
              note: "平台标记已完成区域定位。",
              pipelineTrace: {
                schemaVersion: "watermark_pipeline_trace_v1",
                totalElapsedMs: 20,
                stages: [{
                  id: "registry",
                  label: "平台注册表检索",
                  status: "hit",
                  elapsedMs: 10,
                  summary: "命中示例平台标记",
                  details: { internalEndpoint: "should-not-leave-browser" },
                }],
              },
            },
          },
        },
      },
    },
  }));
  await page.addInitScript(() => {
    const state = window as typeof window & { __reportQaStreamRequests?: Array<Record<string, unknown>> };
    state.__reportQaStreamRequests = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
      if (!url.includes("/v2-api/report-qa/stream")) return originalFetch(input, init);

      const payload = typeof init?.body === "string"
        ? JSON.parse(init.body) as Record<string, unknown>
        : {};
      state.__reportQaStreamRequests!.push(payload);
      const requestNumber = state.__reportQaStreamRequests!.length;
      const firstRequest = requestNumber === 1;
      const webRequest = requestNumber === 3;
      const answer = firstRequest
        ? "报告在右下角定位到平台水印，并与生成痕迹相互印证。"
        : webRequest
          ? "图片本身的检测结论不变；公开报道显示，这段配文属于网友恶搞，目前没有可靠来源支持该事件。[1]"
          : "这里的 91% 是本次报告的风险分，不代表绝对事实。";
      const firstDelta = firstRequest
        ? "报告在右下角定位到"
        : webRequest
          ? "图片本身的检测结论不变；"
          : "这里的 91% 是本次报告的";
      const secondDelta = answer.slice(firstDelta.length);
      const evidenceRefs = firstRequest ? ["平台水印"] : ["视觉线索 1"];
      const webSearch = webRequest ? {
        attempted: true,
        used: true,
        status: "success",
        claim: "特朗普爱上高市早苗",
        query: "特朗普 高市早苗 恶搞",
        contentVerdict: "satire_likely",
        sourceRefs: [1],
        sources: [{
          index: 1,
          title: "官方活动记录未支持相关配文",
          url: "https://example.com/fact-check",
          siteName: "示例事实核查",
          domain: "example.com",
          quality: "major",
          matchLevel: "direct",
        }],
      } : undefined;
      const encoder = new TextEncoder();
      const event = (name: string, data: Record<string, unknown>) => (
        `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
      );
      const timers: number[] = [];

      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(event("start", { grounded: true })));
          if (webSearch) {
            controller.enqueue(encoder.encode(event("status", {
              stage: "claim",
              message: "正在识别图片中需要核验的公开信息",
            })));
            controller.enqueue(encoder.encode(event("sources", { webSearch })));
          }
          timers.push(window.setTimeout(() => {
            controller.enqueue(encoder.encode(event("delta", { text: firstDelta })));
          }, webRequest ? 30 : 50));
          timers.push(window.setTimeout(() => {
            controller.enqueue(encoder.encode(event("delta", { text: secondDelta })));
          }, webRequest ? 80 : 750));
          timers.push(window.setTimeout(() => {
            controller.enqueue(encoder.encode(event("done", {
              answer,
              evidenceRefs,
              suggestedQuestions: ["这个风险分应该怎么理解？"],
              grounded: true,
              webSearch,
              usage: { totalTokens: 80 },
            })));
            controller.close();
          }, webRequest ? 120 : 900));
        },
        cancel() {
          timers.forEach((timer) => window.clearTimeout(timer));
        },
      });
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream; charset=utf-8" },
      });
    };
  });

  await page.goto("/?workspace=1");
  await page.locator('input[type="file"]').setInputFiles({
    name: "qa-review.png",
    mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
  });

  const dock = page.locator(".composer-dock.is-report-chat");
  const composer = dock.getByRole("textbox", { name: "向小鉴询问本次检测报告" });
  await expect(composer).toBeVisible();
  await expect(dock.getByRole("button", { name: "上传新的内容" })).toBeVisible();
  const dockBox = await dock.boundingBox();
  expect(dockBox).not.toBeNull();
  expect(dockBox!.y + dockBox!.height).toBeLessThanOrEqual(845);
  expect(dockBox!.y).toBeGreaterThan(640);

  await dock.getByRole("button", { name: "为什么判断为 AI 生成？" }).click();
  const qa = page.locator(".report-qa");
  await expect(qa.getByRole("heading", { name: "报告问答" })).toBeVisible();
  const streamingParagraph = qa.locator(".report-qa-message.is-assistant.is-streaming p");
  await expect.poll(async () => Array.from((await streamingParagraph.textContent()) || "").length).toBeGreaterThan(0);
  const earlyAnswer = Array.from((await streamingParagraph.textContent()) || "");
  expect(earlyAnswer.length).toBeLessThan(Array.from("报告在右下角定位到").length);
  await expect(qa.getByText("报告在右下角定位到", { exact: true })).toBeVisible();
  await expect(qa.locator(".report-qa-stream-cursor")).toBeVisible();
  await expect(qa.getByText("报告在右下角定位到平台水印，并与生成痕迹相互印证。")).toHaveCount(0);
  await expect(qa.getByText("报告在右下角定位到平台水印，并与生成痕迹相互印证。")).toBeVisible();
  await expect(qa.locator(".report-qa-stream-cursor")).toHaveCount(0);
  await expect(qa.getByText("平台水印", { exact: true })).toBeVisible();
  const requests = await page.evaluate(() => (
    (window as typeof window & { __reportQaStreamRequests?: Array<Record<string, unknown>> })
      .__reportQaStreamRequests || []
  ));
  expect(requests).toHaveLength(1);
  const firstReport = requests[0].report as Record<string, unknown>;
  expect(JSON.stringify(firstReport)).not.toContain("should-not-leave-browser");
  expect(JSON.stringify(firstReport)).not.toContain("image_url");
  expect(JSON.stringify(firstReport)).not.toContain("data:image");

  await composer.fill("这个风险分应该怎么理解？");
  await composer.press("Enter");
  await expect(qa.getByText("这里的 91% 是本次报告的风险分，不代表绝对事实。")).toBeVisible();
  const followUpRequests = await page.evaluate(() => (
    (window as typeof window & { __reportQaStreamRequests?: Array<Record<string, unknown>> })
      .__reportQaStreamRequests || []
  ));
  expect(followUpRequests).toHaveLength(2);
  expect(followUpRequests[0].conversationId).toBeTruthy();
  expect(followUpRequests[1].conversationId).toBe(followUpRequests[0].conversationId);
  expect(followUpRequests[0].turnId).toBeTruthy();
  expect(followUpRequests[1].turnId).not.toBe(followUpRequests[0].turnId);
  expect(followUpRequests[0].media).toEqual({
    type: "image",
    fileName: "qa-review.png",
    legacyDetectionId: 902,
  });
  expect(followUpRequests[1].history).toEqual([
    { role: "user", content: "为什么判断为 AI 生成？" },
    { role: "assistant", content: "报告在右下角定位到平台水印，并与生成痕迹相互印证。" },
  ]);

  await composer.fill("请联网核验：特朗普爱上高市早苗是真的吗？");
  await composer.press("Enter");
  await expect(qa.getByText("更像戏仿或恶搞", { exact: true })).toBeVisible();
  const source = qa.getByRole("link", { name: /官方活动记录未支持相关配文/ });
  await expect(source).toBeVisible();
  await expect(source).toHaveAttribute("href", "https://example.com/fact-check");
  await expect(source.getByText("直接相关", { exact: true })).toBeVisible();
  const webRequests = await page.evaluate(() => (
    (window as typeof window & { __reportQaStreamRequests?: Array<Record<string, unknown>> })
      .__reportQaStreamRequests || []
  ));
  expect(webRequests).toHaveLength(3);
  expect(webRequests[2].webSearch).toEqual({ mode: "auto" });
  const webMedia = webRequests[2].media as Record<string, unknown>;
  expect(String(webMedia.searchImage || "")).toMatch(/^data:image\/jpeg;base64,/);
  expect(await readableTextOffenders(page, ".report-qa")).toEqual([]);
  expect(await readableTextOffenders(page, ".composer-dock")).toEqual([]);
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  const desktopDockBox = await dock.boundingBox();
  const desktopComposerBox = await dock.locator(".report-qa-composer").boundingBox();
  expect(desktopDockBox).not.toBeNull();
  expect(desktopComposerBox).not.toBeNull();
  expect(desktopDockBox!.y + desktopDockBox!.height).toBeLessThanOrEqual(901);
  expect(desktopDockBox!.y).toBeGreaterThan(730);
  expect(desktopComposerBox!.width).toBeLessThanOrEqual(902);
  await expectNoHorizontalOverflow(page);
});

test("检测进度卡使用清晰的正文级字号", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await installBaseMocks(page);
  const runningJob = {
    id: "layout-progress-job",
    version: "1",
    status: "running",
    progress: 38,
    publicStage: "authenticity_analysis",
    result: null,
  };
  await page.route("**/image_upload/detect_async", (route) => route.fulfill({ json: { status: "success", job: runningJob } }));
  await page.route("**/image_upload/jobs/layout-progress-job**", (route) => route.fulfill({
    status: 429,
    headers: { "Retry-After": "10" },
    json: { status: "error", error: "rate_limited", message: "请稍候" },
  }));

  await page.goto("/?workspace=1");
  await page.locator(".guest-upload-consent input").check();
  await page.locator('input[type="file"]').setInputFiles({
    name: "progress-typography.png",
    mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
  });

  const panel = page.locator(".progress-panel");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".progress-heading strong")).toHaveText(/正在核验内容真实性|任务仍在运行/);
  const agentArtwork = page.locator(".agent-progress-message .brand-agent-portrait");
  const scanArtwork = panel.locator(".progress-scan-artwork > .analysis-mode-mark");
  await expect(agentArtwork).toBeVisible();
  await expect(scanArtwork).toBeVisible();
  await expect(agentArtwork).toHaveAttribute("viewBox", "0 0 48 48");
  await expect(scanArtwork).toHaveAttribute("viewBox", "0 0 56 56");
  expect((await scanArtwork.boundingBox())?.width).toBeGreaterThanOrEqual(32);
  await expect(panel.locator(".progress-heading .spin")).toHaveCount(0);
  await expect(panel.locator(".progress-heading .analysis-mode-mark")).toHaveCount(1);
  const stageNodes = panel.locator(".progress-system > span > i");
  const stageGlyphs = stageNodes.locator(":scope > .progress-stage-glyph");
  await expect(stageNodes).toHaveCount(3);
  await expect(stageGlyphs).toHaveCount(3);
  await expect(panel.locator(".progress-system .brand-art-icon")).toHaveCount(0);
  const stageGeometry = await stageNodes.evaluateAll((nodes) => nodes.map((node) => {
    const nodeBox = node.getBoundingClientRect();
    const glyphBox = node.querySelector<HTMLElement>(".progress-stage-glyph")!.getBoundingClientRect();
    return {
      nodeWidth: nodeBox.width,
      nodeHeight: nodeBox.height,
      glyphWidth: glyphBox.width,
      glyphHeight: glyphBox.height,
      centerDeltaX: Math.abs((nodeBox.left + nodeBox.width / 2) - (glyphBox.left + glyphBox.width / 2)),
      centerDeltaY: Math.abs((nodeBox.top + nodeBox.height / 2) - (glyphBox.top + glyphBox.height / 2)),
    };
  }));
  for (const geometry of stageGeometry) {
    expect(Math.round(geometry.nodeWidth)).toBeGreaterThanOrEqual(36);
    expect(Math.round(geometry.nodeHeight)).toBeGreaterThanOrEqual(36);
    expect(geometry.glyphWidth).toBe(18);
    expect(geometry.glyphHeight).toBe(18);
    expect(geometry.centerDeltaX).toBeLessThanOrEqual(1);
    expect(geometry.centerDeltaY).toBeLessThanOrEqual(2);
  }
  const sizes = await panel.evaluate((element) => {
    const size = (selector: string) => Number.parseFloat(getComputedStyle(element.querySelector<HTMLElement>(selector)!).fontSize);
    return {
      title: size(".progress-heading strong"),
      detail: size(".progress-heading p"),
      percent: size(".progress-heading b"),
      stage: size(".progress-system b"),
      stageNote: size(".progress-system small"),
      stopButton: size(".cancel-analysis-button"),
      stopNote: size(".stop-waiting-note"),
    };
  });
  expect(sizes.title).toBeGreaterThanOrEqual(15);
  expect(sizes.detail).toBeGreaterThanOrEqual(13);
  expect(sizes.percent).toBeGreaterThanOrEqual(14);
  expect(sizes.stage).toBeGreaterThanOrEqual(12);
  expect(sizes.stageNote).toBeGreaterThanOrEqual(11);
  expect(sizes.stopButton).toBeGreaterThanOrEqual(14);
  expect(sizes.stopNote).toBeGreaterThanOrEqual(12);
  expect(await readableTextOffenders(page, ".progress-panel")).toEqual([]);
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileSizes = await panel.evaluate((element) => {
    const size = (selector: string) => Number.parseFloat(getComputedStyle(element.querySelector<HTMLElement>(selector)!).fontSize);
    return {
      title: size(".progress-heading strong"),
      detail: size(".progress-heading p"),
      stage: size(".progress-system b"),
      stageNote: size(".progress-system small"),
    };
  });
  expect(mobileSizes.title).toBeGreaterThanOrEqual(14);
  expect(mobileSizes.detail).toBeGreaterThanOrEqual(12);
  expect(mobileSizes.stage).toBeGreaterThanOrEqual(12);
  expect(mobileSizes.stageNote).toBeGreaterThanOrEqual(11);
  await expectNoHorizontalOverflow(page);
  await panel.getByRole("button", { name: "停止等待" }).click();
});

for (const viewport of viewports.slice(0, 2)) {
  test(`鉴伪入口在 ${viewport.name} 视口居中且控件不重叠`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installBaseMocks(page);
    await page.goto("/?workspace=1");

    await expectHorizontalHeading(page, ".upload-stage h3");
    await expectBoxesDoNotOverlap(page, ".compact-capability-strip > div");
    await expectCapabilityContentDoesNotOverlap(page);
    const workspace = await page.locator(".agent-workspace").boundingBox();
    const upload = await page.locator(".upload-stage").boundingBox();
    expect(workspace).not.toBeNull();
    expect(upload).not.toBeNull();
    expect(Math.abs((upload!.x + upload!.width / 2) - (workspace!.x + workspace!.width / 2))).toBeLessThanOrEqual(2);
    expect(Math.abs((upload!.x + upload!.width / 2) - viewport.width / 2), "匿名上传入口未相对整个视口居中").toBeLessThanOrEqual(2);
    await expect(page.locator(".sidebar-desktop")).toHaveCount(0);
    await expect(page.locator(".mobile-history-button")).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveAttribute("accept", /application\/pdf/);
    const homeButton = page.getByRole("button", { name: "返回慧鉴AI官网首页" });
    await expect(homeButton).toBeVisible();
    if (viewport.name === "mobile") {
      const brandDimensions = await homeButton.evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }));
      const pickerBox = await page.getByRole("button", { name: "选择图片检测模型" }).boundingBox();
      const homeBox = await homeButton.boundingBox();
      expect(brandDimensions.scrollWidth, "移动端品牌按钮内容越界").toBeLessThanOrEqual(brandDimensions.clientWidth);
      expect(pickerBox).not.toBeNull();
      expect(homeBox).not.toBeNull();
      expect(pickerBox!.x - (homeBox!.x + homeBox!.width), "品牌按钮与模型选择器间距不足").toBeGreaterThanOrEqual(8);
    }

    await page.getByRole("button", { name: "选择图片检测模型" }).click();
    const modelMenu = page.getByRole("listbox", { name: "图片检测模型" });
    await expect(modelMenu).toBeVisible();
    const menuBox = await modelMenu.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(0);
    expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(await readableTextOffenders(page, ".analysis-model-picker")).toEqual([]);
    await expectNoHorizontalOverflow(page);
    await page.keyboard.press("Escape");
    await homeButton.click();
    await expect(page.locator(".home-v3")).toBeVisible();
  });
}

test("手机工作台在 320 至 430px 间保持连续布局且首屏可上传", async ({ page }) => {
  await installBaseMocks(page);
  const mobileViewports = [
    { width: 320, height: 568 },
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 430, height: 932 },
  ];

  for (const viewport of mobileViewports) {
    await page.setViewportSize(viewport);
    await page.goto("/?workspace=1");
    await expect(page.locator(".topbar-login")).toBeVisible();
    await expect(page.locator(".agent-topbar .brand-copy")).toBeHidden();

    await expectNoInternalOverflow(page, [
      ".agent-topbar",
      ".topbar-title",
      ".analysis-model-picker",
      ".analysis-model-trigger",
      ".topbar-actions",
    ]);
    await expectTouchTargets(page, [
      ".agent-topbar .brand-home-button",
      ".analysis-model-trigger",
      ".topbar-login",
      ".upload-button",
      ".guest-upload-consent",
    ]);

    const layout = await page.evaluate(() => {
      const upload = document.querySelector<HTMLElement>(".upload-stage")!.getBoundingClientRect();
      const action = document.querySelector<HTMLElement>(".upload-button")!.getBoundingClientRect();
      const avatarFrame = document.querySelector<HTMLElement>(".upload-stage-icon")!.getBoundingClientRect();
      const avatar = document.querySelector<HTMLElement>(".upload-stage-icon .brand-agent-avatar")!.getBoundingClientRect();
      const uploadTitle = document.querySelector<HTMLElement>(".upload-stage h3")!.getBoundingClientRect();
      const capabilities = Array.from(document.querySelectorAll<HTMLElement>(".compact-capability-strip > div"), (element) => {
        const bounds = element.getBoundingClientRect();
        return { top: bounds.top, left: bounds.left, right: bounds.right };
      });
      return {
        uploadHeight: upload.height,
        actionBottom: action.bottom,
        avatarFrame: { width: avatarFrame.width, height: avatarFrame.height },
        avatar: { width: avatar.width, height: avatar.height, bottom: avatar.bottom },
        uploadTitleTop: uploadTitle.top,
        capabilities,
      };
    });
    expect(layout.uploadHeight, `${viewport.width}px 上传卡异常增高`).toBeLessThan(600);
    expect(layout.actionBottom, `${viewport.width}px 首屏未完整展示上传按钮`).toBeLessThanOrEqual(viewport.height);
    expect(layout.capabilities).toHaveLength(3);
    expect(Math.max(...layout.capabilities.map((item) => item.top)) - Math.min(...layout.capabilities.map((item) => item.top))).toBeLessThanOrEqual(1);
    expect(layout.capabilities[0].right).toBeLessThanOrEqual(layout.capabilities[1].left + 1);
    expect(layout.capabilities[1].right).toBeLessThanOrEqual(layout.capabilities[2].left + 1);
    expect(layout.avatar.width, `${viewport.width}px 上传头像横向溢出`).toBeLessThanOrEqual(layout.avatarFrame.width + 1);
    expect(layout.avatar.height, `${viewport.width}px 上传头像纵向溢出`).toBeLessThanOrEqual(layout.avatarFrame.height + 1);
    expect(layout.avatar.bottom, `${viewport.width}px 上传头像压住标题`).toBeLessThanOrEqual(layout.uploadTitleTop);
    await expectNoHorizontalOverflow(page);
  }
});

test("手机登录弹窗保持清晰字号、完整主操作与触控尺寸", async ({ page }) => {
  await installBaseMocks(page);
  const mobileViewports = [
    { width: 320, height: 568 },
    { width: 390, height: 844 },
    { width: 520, height: 761 },
  ];

  for (const viewport of mobileViewports) {
    await page.setViewportSize(viewport);
    await page.goto("/?workspace=1");
    await page.locator(".topbar-login").click();
    const dialog = page.locator(".auth-dialog");
    await expect(dialog).toBeVisible();
    await page.waitForTimeout(300);

    const typography = await page.evaluate(() => ({
      title: Number.parseFloat(getComputedStyle(document.querySelector<HTMLElement>(".auth-heading h2")!).fontSize),
      input: Number.parseFloat(getComputedStyle(document.querySelector<HTMLElement>(".field-shell input")!).fontSize),
    }));
    expect(typography.title).toBeGreaterThanOrEqual(26);
    expect(typography.input).toBeGreaterThanOrEqual(16);
    expect(await readableTextOffenders(page, ".auth-dialog", 13)).toEqual([]);

    await expectNoInternalOverflow(page, [
      ".auth-dialog",
      ".auth-brand-row",
      ".auth-panels",
      ".auth-mode-switch",
      ".field-shell",
      ".terms-check",
    ]);
    await expectTouchTargets(page, [
      ".auth-dialog .dialog-close",
      ".auth-panels button",
      ".auth-mode-switch button",
      ".password-visibility",
      ".terms-check",
      ".auth-submit",
    ]);
    await expectNoHorizontalOverflow(page);

    if (viewport.height <= 640) {
      await expect(page.locator(".auth-privacy-details")).toBeHidden();
      const submit = await page.locator(".auth-submit").boundingBox();
      expect(submit, "短屏登录主按钮不存在").not.toBeNull();
      expect(submit!.y + submit!.height, "短屏首屏未完整展示登录按钮").toBeLessThanOrEqual(viewport.height);
    } else {
      await expect(page.locator(".auth-privacy-details")).toBeVisible();
    }

    await page.locator(".auth-dialog .dialog-close").click();
    await expect(dialog).toBeHidden();
  }
});

test("注册页即时提示密码问题并展示真实短信提交状态", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  let smsCalls = 0;
  await page.route("**/sms/send_code", async (route) => {
    smsCalls += 1;
    const payload = route.request().postDataJSON() as { phone: string; scene: string };
    expect(payload).toEqual({ phone: "13800000000", scene: "register" });
    if (smsCalls === 1) {
      await route.fulfill({
        status: 502,
        json: {
          success: false,
          code: "sms_submit_failed",
          message: "短信未能发送，请稍后重试；若持续失败，请联系管理员",
        },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: {
        success: true,
        delivery_status: "submitted",
        message: "验证码已提交，通常会在 1 分钟内送达",
        expires_in: 300,
        resend_in: 60,
      },
    });
  });

  await page.goto("/?workspace=1");
  await page.locator(".topbar-login").click();
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByPlaceholder("怎么称呼你").fill("测试用户");
  await page.getByPlaceholder("请输入手机号").fill("13800000000");
  const passwordInputs = page.locator('input[autocomplete="new-password"]');
  await passwordInputs.nth(0).fill("abcdefg");
  await passwordInputs.nth(1).fill("abcdefh");
  await page.locator(".terms-check input").check({ force: true });
  await page.getByRole("button", { name: "创建账号" }).click();

  await expect(page.locator("#password-error")).toContainText("不到 8 位");
  await expect(page.locator("#password-confirm-error")).toHaveText("两次输入的密码不一致");
  await expect(page.locator(".password-requirements .met")).toHaveCount(1);

  await passwordInputs.nth(0).fill("Password123");
  await passwordInputs.nth(1).fill("Password123");
  await expect(page.locator("#password-error")).toHaveCount(0);
  await expect(page.locator("#password-confirm-error")).toHaveCount(0);
  await expect(page.locator(".password-requirements .met")).toHaveCount(3);

  await page.getByRole("button", { name: "获取验证码" }).click();
  await expect(page.locator(".auth-message.error")).toContainText("短信未能发送");
  await page.getByRole("button", { name: "获取验证码" }).click();
  await expect(page.locator(".auth-message.success")).toContainText("验证码已提交");
  await expect(page.locator(".sms-delivery-help")).toContainText("还没收到");
  await expect(page.getByRole("button", { name: "60s" })).toBeDisabled();
  expect(smsCalls).toBe(2);
});

test("已注册手机号在注册入口可切换验证码登录并完成密码重置", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  const smsScenes: string[] = [];
  let resetPayload: Record<string, string> | null = null;
  await page.route("**/sms/send_code", async (route) => {
    const payload = route.request().postDataJSON() as { phone: string; scene: string };
    smsScenes.push(payload.scene);
    if (payload.scene === "register") {
      await route.fulfill({
        status: 409,
        json: {
          success: false,
          code: "account_exists",
          account_status: "registered",
          message: "该手机号已注册，请切换到验证码登录；忘记密码可直接重置",
          actions: ["sms_login", "reset_password"],
        },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: {
        success: true,
        delivery_status: "submitted",
        message: "验证码已提交，通常会在 1 分钟内送达",
        expires_in: 300,
        resend_in: 60,
      },
    });
  });
  await page.route("**/api/password/reset", async (route) => {
    resetPayload = route.request().postDataJSON() as Record<string, string>;
    await route.fulfill({ status: 200, json: { status: "success", message: "密码已重置，请使用新密码登录" } });
  });

  await page.goto("/?workspace=1");
  await page.locator(".topbar-login").click();
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByPlaceholder("请输入手机号").fill("19730015809");
  await page.getByRole("button", { name: "获取验证码" }).click();

  const guidance = page.locator(".auth-account-guidance");
  await expect(guidance).toBeVisible();
  await expect(guidance).toContainText("这个手机号已经注册");
  await guidance.getByRole("button", { name: "验证码登录" }).click();
  await expect(page.getByRole("button", { name: "验证码登录" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByPlaceholder("请输入手机号")).toHaveValue("19730015809");

  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByRole("button", { name: "获取验证码" }).click();
  await page.locator(".auth-account-guidance").getByRole("button", { name: "忘记密码" }).click();
  await expect(page.getByRole("heading", { name: "重置登录密码" })).toBeVisible();
  await page.getByRole("button", { name: "获取验证码" }).click();
  const newPasswords = page.locator('input[autocomplete="new-password"]');
  await newPasswords.nth(0).fill("NewPassword123");
  await newPasswords.nth(1).fill("NewPassword123");
  await page.getByPlaceholder("输入验证码").fill("246810");
  await page.getByRole("button", { name: "确认修改密码" }).click();

  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
  await expect(page.locator(".auth-message.success")).toContainText("密码已修改");
  expect(smsScenes).toEqual(["register", "register", "reset"]);
  expect(resetPayload).toEqual({
    phone: "19730015809",
    secret: "NewPassword123",
    secret_confirm: "NewPassword123",
    sms_code: "246810",
  });
  await expectNoHorizontalOverflow(page);
});

test("登录态窄屏保留完整模型名称并移除开发者入口", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await installBaseMocks(page, true);
  await page.goto("/?workspace=1");

  await expect(page.locator(".workspace-account-menu")).toBeVisible();
  await expect(page.locator(".analysis-model-trigger-copy strong")).toHaveText("快速检测");
  await expect(page.locator(".workspace-developer-button")).toBeHidden();
  await expectNoInternalOverflow(page, [
    ".agent-topbar",
    ".topbar-title",
    ".analysis-model-picker",
    ".analysis-model-trigger",
    ".topbar-actions",
  ]);
  await expectTouchTargets(page, [
    ".mobile-history-button",
    ".agent-topbar .brand-home-button",
    ".analysis-model-trigger",
    ".workspace-account-menu .account-menu-trigger",
  ]);
  await expectNoHorizontalOverflow(page);
});

test("登录态访问统计携带同源会话 Cookie", async ({ page, context }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, "webdriver", { configurable: true, get: () => false });
    Object.defineProperty(Navigator.prototype, "userAgent", { configurable: true, get: () => "Mozilla/5.0 Safari/605.1.15" });
  });
  await context.addCookies([{ name: "session", value: "analytics-user", url: "http://127.0.0.1:4173" }]);
  await installBaseMocks(page, true);
  await page.unroute("**/api/analytics/pageview");
  let resolveCaptured!: (value: { cookie: string; body: string }) => void;
  const captured = new Promise<{ cookie: string; body: string }>((resolve) => { resolveCaptured = resolve; });
  const receiver = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    request.on("end", () => {
      resolveCaptured({ cookie: request.headers.cookie || "", body: Buffer.concat(chunks).toString("utf8") });
      response.writeHead(204);
      response.end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    receiver.once("error", reject);
    receiver.listen(45873, "127.0.0.1", resolve);
  });
  try {
    await page.goto("/?workspace=1");
    const request = await captured;
    expect(request.cookie).toContain("session=analytics-user");
    expect(JSON.parse(request.body).page).toBe("workspace");
  } finally {
    await new Promise<void>((resolve, reject) => receiver.close((error) => error ? reject(error) : resolve()));
  }
});

test("登录用户可以隐藏并恢复最近任务侧栏", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await page.goto("/?workspace=1");

  const sidebar = page.locator(".sidebar-desktop");
  const topbarBrand = page.locator(".agent-topbar .brand-home-button");
  await expect(sidebar).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "返回慧鉴AI官网首页" })).toBeVisible();
  await expect(topbarBrand).toBeHidden();
  await page.getByRole("button", { name: "隐藏最近任务" }).click();
  await expect(sidebar).toBeHidden();
  await expect(topbarBrand).toBeVisible();
  const restore = page.getByRole("button", { name: "显示最近任务" });
  await expect(restore).toBeVisible();
  await restore.click();
  await expect(sidebar).toBeVisible();
  await expect(topbarBrand).toBeHidden();
  await expectNoHorizontalOverflow(page);
});

test("模型选择器完整加载统一矢量模式图标", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await page.goto("/?workspace=1");

  const picker = page.locator(".analysis-model-picker");
  const triggerArtwork = picker.locator(".analysis-model-trigger .analysis-mode-mark");
  await expect(triggerArtwork).toBeVisible();
  await expect(triggerArtwork).toHaveAttribute("viewBox", "0 0 56 56");
  await page.getByRole("button", { name: "选择图片检测模型" }).click();
  const menuArtwork = picker.locator(".analysis-model-menu .analysis-mode-mark");
  await expect(menuArtwork).toHaveCount(2);
  expect(await menuArtwork.evaluateAll((icons) => icons.every((icon) => icon.getAttribute("viewBox") === "0 0 56 56"))).toBeTruthy();
  await expect(picker.locator(".brand-art-icon")).toHaveCount(0);
});

test("工作台账户使用明确的用户图标且顶部不重复放置开发者入口", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await page.goto("/?workspace=1");

  const accountButton = page.locator(".workspace-account-menu .account-menu-trigger");
  const accountArtwork = accountButton.locator(".account-control-artwork");
  const developerButton = page.locator(".workspace-developer-button");

  await expect(accountArtwork).toBeVisible();
  await expect(developerButton).toHaveCount(0);
  await expect(accountArtwork).toHaveAttribute("role", "img");
  const accountAvatarImage = accountArtwork.locator(".brand-user-avatar-art");
  await expect(accountAvatarImage).toHaveCount(1);
  await expect(accountAvatarImage).toHaveAttribute("src", /^data:image\/svg\+xml/);
  await expect.poll(() => accountAvatarImage.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBeTruthy();
  await expect(accountArtwork.locator(".brand-user-avatar-letter")).toHaveCount(0);
  await expect(accountButton.locator("svg")).toHaveCount(0);
  const sidebarAvatar = page.locator(".sidebar-account .brand-user-avatar");
  await expect(sidebarAvatar).toHaveAttribute("role", "img");
  await expect(sidebarAvatar.locator(".brand-user-avatar-art")).toHaveAttribute("src", /^data:image\/svg\+xml/);
  await expectNoHorizontalOverflow(page);
});

test("开发者平台移动布局与 API Key 弹窗满足键盘交互", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/?developer=1");

  await expect(page.getByRole("heading", { name: "把慧鉴AI接入你的业务流程" })).toBeVisible();
  await expect(page.getByRole("button", { name: "API 密钥" })).toBeVisible();
  await expect(page.getByRole("button", { name: "返回慧鉴AI官网" })).toBeVisible();
  await expect(page.getByRole("button", { name: "官网", exact: true })).toHaveCount(0);
  expect(await readableTextOffenders(page, ".developer-shell")).toEqual([]);
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "API 密钥" }).click();
  await expect(page).toHaveURL(/developerTab=keys/);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "把慧鉴AI接入你的业务流程" })).toBeVisible();
  await page.getByRole("button", { name: "API 密钥" }).click();
  const createButton = page.getByRole("button", { name: "创建 API Key" });
  await createButton.click();
  const dialog = page.getByRole("dialog", { name: "创建 API Key" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("有效期")).toHaveValue("90");
  await expect(dialog.getByRole("option", { name: "永不过期" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "关闭" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "创建 Key" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(createButton).toBeFocused();
});

test("开发者平台在手机横屏仍可滚动访问导航", async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/?developer=1");

  await expect(page.getByRole("heading", { name: "把慧鉴AI接入你的业务流程" })).toBeVisible();
  await expect(page.getByRole("button", { name: "API 密钥" })).toBeVisible();
  await expect(page.getByRole("button", { name: "接入文档" })).toBeVisible();
  expect(await readableTextOffenders(page, ".developer-shell")).toEqual([]);
  await expectNoHorizontalOverflow(page);
});

test("开发者接入文档可切换视频接口并生成可复制示例", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/?developer=1&developerTab=docs");

  await expect(page.getByRole("heading", { name: "图像与视频鉴伪接入" })).toBeVisible();
  await page.getByRole("button", { name: /视频鉴伪/ }).click();
  await expect(page.getByText("/api/openapi/v1/video-detections", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/单文件不超过 200 MB/)).toBeVisible();

  const codePanel = page.getByRole("tabpanel");
  await expect(codePanel).toContainText('-F "video=@./sample.mp4"');
  await expect(codePanel).not.toContainText('-F "mode=');
  await page.getByRole("tab", { name: "Python" }).click();
  await expect(codePanel).toContainText("/api/openapi/v1/video-detections");
  await expect(codePanel).toContainText('files={"video": media_file}');
  await expectNoHorizontalOverflow(page);
});

test("Agent Skill 提供一句话接入、使用示例与完整客户端标识", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/?developer=1&developerTab=skill");

  await expect(page.getByRole("heading", { name: "一句话，让你的 Agent 学会鉴伪" })).toBeVisible();
  await expect(page.getByRole("button", { name: "复制 Agent Skill 安装指令" })).toBeVisible();
  await expect(page.getByText("从复制到第一次检测，只需三步")).toBeVisible();
  await expect(page.getByText("这些 Agent 都能接入")).toBeVisible();
  await expect(page.locator(".agent-skill-client-grid").getByText("OpenClaw", { exact: true })).toBeVisible();
  await expect(page.locator(".agent-skill-client-grid").getByText("龙虾 Agent", { exact: true })).toBeVisible();

  const clientLogos = page.locator(".agent-skill-client-grid img");
  await expect(clientLogos).toHaveCount(10);
  expect(await clientLogos.evaluateAll((images: HTMLImageElement[]) => images.every((image) => image.complete && image.naturalWidth > 0))).toBeTruthy();
  await expect(page.locator(".agent-skill-relay-agents img")).toHaveCount(5);

  await page.getByRole("button", { name: "复制 Agent Skill 安装指令" }).click();
  await expect(page.getByRole("button", { name: "复制 Agent Skill 安装指令" })).toContainText("已复制，可以发送了");
  expect(await readableTextOffenders(page, ".developer-agent-skill-page")).toEqual([]);
  await expectNoHorizontalOverflow(page);

  const guideResponse = await request.get("/huijian-skill.md");
  expect(guideResponse.ok()).toBeTruthy();
  expect(await guideResponse.text()).toContain("Install the Huijian AI Image Forensics Skill");
  const skillResponse = await request.get("/skills/huijian-image-forensics/SKILL.md");
  expect(skillResponse.ok()).toBeTruthy();
  expect(await skillResponse.text()).toContain("name: huijian-image-forensics");
});

test("Agent Skill 手机页面保持可读并允许横向浏览开发者导航", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page);
  await page.goto("/?developer=1&developerTab=skill");

  await expect(page.getByRole("heading", { name: "一句话，让你的 Agent 学会鉴伪" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Agent Skill", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "复制 Agent Skill 安装指令" })).toBeVisible();
  const copyButtonBox = await page.getByRole("button", { name: "复制 Agent Skill 安装指令" }).boundingBox();
  expect(copyButtonBox?.height || 0).toBeGreaterThanOrEqual(44);
  const skillPageBox = await page.locator(".developer-agent-skill-page").boundingBox();
  const copyPanelBox = await page.locator(".agent-skill-copy-panel").boundingBox();
  expect(skillPageBox ? skillPageBox.x + skillPageBox.width : Number.POSITIVE_INFINITY).toBeLessThanOrEqual(390);
  expect(copyPanelBox ? copyPanelBox.x + copyPanelBox.width : Number.POSITIVE_INFINITY).toBeLessThanOrEqual(390);
  await expect(page.locator(".agent-skill-client-grid article")).toHaveCount(10);
  expect(await readableTextOffenders(page, ".developer-agent-skill-page")).toEqual([]);
  await expectNoHorizontalOverflow(page);
});

test("开发者凭据读取失败不会伪装成空列表", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true);
  await installDeveloperMocks(page, { keys: true });
  await page.goto("/?developer=1&developerTab=keys");

  await expect(page.getByText("API Key 列表读取失败，为避免重复创建，当前已暂停凭据操作")).toBeVisible();
  await expect(page.getByRole("button", { name: "创建 API Key" })).toBeDisabled();
  await expect(page.getByText("尚未创建 API Key")).toHaveCount(0);
});

test("历史记录读取失败时提供可执行的重试入口", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBaseMocks(page, true, true);
  await page.goto("/?workspace=1");

  await expect(page.getByText("个人历史暂时无法读取，请稍后刷新")).toBeVisible();
  const retry = page.getByRole("button", { name: "重新加载" });
  await expect(retry).toBeVisible();
  const retriedRequest = page.waitForRequest(/\/v2-api\/history/);
  await retry.click();
  await retriedRequest;
  await expect(page.getByText("个人历史暂时无法读取，请稍后刷新")).toBeVisible();
});
