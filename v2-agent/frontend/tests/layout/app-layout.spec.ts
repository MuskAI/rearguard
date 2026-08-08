import { expect, Page, test } from "@playwright/test";
import { createServer } from "node:http";

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

test("移动官网导航支持键盘关闭并恢复焦点", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBaseMocks(page);
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "打开网站导航" });
  await trigger.click();
  const navigation = page.getByRole("navigation", { name: "移动端官网导航" });
  await expect(navigation).toBeVisible();
  await expect(navigation.locator("a").first()).toBeFocused();
  await expect(navigation.getByRole("button", { name: "开发者概览" })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "在线调试" })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "接入文档" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("navigation", { name: "移动端官网导航" })).toBeHidden();
  await expect(trigger).toBeFocused();
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

test("结果页完整依据入口使用正文级字号", async ({ page }) => {
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
  const disclosure = page.locator(".rationale-disclosure").first();
  await expect(disclosure).toBeVisible();
  const sizes = await disclosure.locator("summary").evaluate((summary) => ({
    summary: Number.parseFloat(getComputedStyle(summary).fontSize),
    count: Number.parseFloat(getComputedStyle(summary.querySelector("span")!).fontSize),
  }));
  expect(sizes.summary).toBeGreaterThanOrEqual(17);
  expect(sizes.count).toBeGreaterThanOrEqual(14);
  expect(await readableTextOffenders(page, ".agent-result")).toEqual([]);
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
