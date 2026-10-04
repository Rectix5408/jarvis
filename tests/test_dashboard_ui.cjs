// SPDX-License-Identifier: Apache-2.0
// Static preview must be running; API responses are fixtures only inside this test.
const assert = require("node:assert/strict");
const { createRequire } = require("node:module");
const external = process.env.DASHBOARD_NODE_MODULES;
const dependency = external
  ? createRequire(`${external}/package.json`)
  : require;
const { chromium } = dependency("playwright");
const { PNG } = dependency("pngjs");
const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8766";
const files = Array.from({ length: 360 }, (_, i) => ({
  path: `data/file-${i}.md`,
  name: i === 0 ? "<img src=x onerror=alert(1)>.md" : `Datei-${i}.md`,
  groups: [{ id: `g${i % 5}`, name: `Gruppe ${i % 5}` }],
}));
let assertions = 0;
function check(condition, message) {
  assert.ok(condition, message);
  assertions++;
}
async function run() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
      { width: 320, height: 740 },
    ]) {
      console.log(`Testing viewport ${viewport.width}`);
      const context = await browser.newContext({ viewport });
      await context.addInitScript(() =>
        localStorage.setItem("jarvis_token", "test-only-token"),
      );
      const page = await context.newPage(),
        errors = [],
        requests = [],
        brokenAssets = [];
      let expired = false,
        rejectFiles = false,
        activated = "",
        refuseActivation = false;
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("response", (response) => {
        if (response.url().includes("/static/") && response.status() >= 400)
          brokenAssets.push(response.url());
      });
      await page.route("**/api/**", async (route) => {
        const request = route.request(),
          path = new URL(request.url()).pathname;
        requests.push(path);
        check(
          request.headers().authorization === "Bearer test-only-token",
          "Authenticated API request",
        );
        if (expired)
          return route.fulfill({ status: 401, json: { error: "expired" } });
        if (path === "/api/me")
          return route.fulfill({ json: { username: "rene", is_admin: false } });
        if (path === "/api/wissen/files")
          return route.fulfill({
            status: rejectFiles ? 403 : 200,
            json: rejectFiles ? { error: "forbidden" } : { files },
          });
        if (path === "/api/cpu") return route.fulfill({ json: { cpu: 23.4 } });
        if (path === "/api/llm/active-status")
          return route.fulfill({ json: { status: "ok" } });
        if (path === "/api/llm/profiles")
          return route.fulfill({
            json: {
              profiles: [
                {
                  id: "local",
                  name: "Ollama",
                  model: "google/gemini-2.0-flash-001",
                },
                { id: "other", name: "Anderes Modell", model: "test-model" },
                { id: "locked", name: "Gesperrt", locked: true },
              ],
              active_id: activated || "local",
            },
          });
        if (path === "/api/llm/profiles/other/activate") {
          if (!refuseActivation) activated = "other";
          return route.fulfill({
            status: refuseActivation ? 403 : 200,
            json: { ok: !refuseActivation },
          });
        }
        throw new Error(`Unexpected API: ${path}`);
      });
      // Only the dashboard is under test; the real chat remains a separate existing application.
      await page.route("**/chat", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: "<h1>Existing chat test fixture</h1>",
        }),
      );
      await page.goto(`${base}/dashboard`);
      await page.waitForFunction(
        () => document.getElementById("file-count").textContent === "360",
      );
      await page.waitForFunction(
        () => Number(document.getElementById("scene").dataset.frames) > 3,
      );
      console.log("Data and canvas ready");
      check(
        await page
          .locator("#model")
          .evaluate((el) => el.scrollWidth <= el.clientWidth),
        "Long model name fits",
      );
      check(
        await page
          .locator("#model")
          .evaluate((el) => parseFloat(getComputedStyle(el).fontSize) <= 14),
        "Compact model typography",
      );
      check(
        (await page.locator("#model-status").getAttribute("data-status")) ===
          "ok",
        "Model status uses semantic state",
      );
      await page.locator("#theme-toggle").click();
      check(
        await page.evaluate(
          () =>
            document.body.classList.contains("light") &&
            localStorage.getItem("jarvis_theme") === "light",
        ),
        "Light theme persists",
      );
      await page.screenshot({
        path: `/tmp/jarvis-dashboard-light-${viewport.width}.png`,
        fullPage: true,
      });
      await page.locator("#theme-toggle").click();
      check(
        (await page.locator("#model").textContent()) ===
          "google/gemini-2.0-flash-001",
        "Real profile model shown",
      );
      check(
        (await page.locator("#files li").count()) === 360,
        "No file feature limit",
      );
      check(
        (await page.locator("#scene").getAttribute("data-nodes")) === "350",
        "Only geometry sampled",
      );
      check(
        (await page.locator("#files img").count()) === 0,
        "Untrusted file names rendered as text",
      );
      check(
        await page.locator("#settings").isHidden(),
        "Admin-only settings hidden",
      );
      check(
        await page.locator("option[value=locked]").isDisabled(),
        "Locked profile cannot be selected",
      );
      check(
        (await page.locator("#cpu").textContent()) === "23 %",
        "Real CPU value",
      );
      check(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        "No horizontal overflow",
      );
      const canvas = page.locator("#scene canvas");
      const first = await canvas.screenshot();
      const png = PNG.sync.read(first);
      let visible = 0;
      for (let i = 0; i < png.data.length; i += 4)
        if (png.data[i] + png.data[i + 1] + png.data[i + 2] > 160) visible++;
      check(visible > 300, "Canvas contains visible graph pixels");
      await page.waitForTimeout(700);
      check(!first.equals(await canvas.screenshot()), "Graph visibly animates");
      await page.locator("#pause").click();
      check(
        (await page.locator("#pause").getAttribute("aria-pressed")) === "true",
        "Animation pause state",
      );
      await page.waitForTimeout(500);
      const still = await canvas.screenshot();
      await page.waitForTimeout(250);
      check(
        still.equals(await canvas.screenshot()),
        "Paused graph stays still",
      );
      const bounds = await canvas.boundingBox();
      await page.mouse.move(
        bounds.x + bounds.width / 2,
        bounds.y + bounds.height / 2,
      );
      await page.mouse.down();
      await page.mouse.move(
        bounds.x + bounds.width / 2 + 70,
        bounds.y + bounds.height / 2,
        { steps: 10 },
      );
      await page.mouse.up();
      await page.waitForTimeout(500);
      check(
        !still.equals(await canvas.screenshot()),
        "Pointer orbit changes canvas",
      );
      console.log("Canvas checks passed");
      await page.locator("#reset").click();
      await page.locator("#search").fill("Datei-359");
      check(
        (await page.locator("#files li").count()) === 1,
        "Search covers files beyond geometry sample",
      );
      await page.locator("#files li button").click();
      check(
        (await page.locator("#selected-name").textContent()) === "Datei-359.md",
        "File selection works",
      );
      await page.locator("#core-mode").click();
      check(
        (await page.locator("#core-mode").getAttribute("aria-pressed")) ===
          "true",
        "Core mode",
      );
      await page.locator("#network-mode").click();
      await page.locator("#search").fill("");
      refuseActivation = true;
      await page.locator("#profile").selectOption("other");
      await page.waitForFunction(
        () => !document.getElementById("profile").disabled,
      );
      check(
        (await page.locator("#profile").inputValue()) === "local",
        "Server rejects unauthorized profile switch",
      );
      refuseActivation = false;
      await page.locator("#profile").selectOption("other");
      await page.waitForFunction(
        () => document.getElementById("model").textContent === "test-model",
      );
      check(
        activated === "other",
        "Profile switch uses existing activation endpoint",
      );
      console.log("Profile checks passed");
      await page.locator("#open-chat").click();
      check(
        await page.locator("#chat-dialog").isVisible(),
        "Chat dialog opens",
      );
      check(
        (await page.locator("#chat-frame").getAttribute("src")) === "/chat",
        "Existing chat reused",
      );
      check(
        (await page.locator("#chat-frame").getAttribute("allow")).includes(
          "microphone",
        ),
        "Microphone allowed for existing flow",
      );
      await page.keyboard.press("Escape");
      check(
        await page.locator("#chat-dialog").isHidden(),
        "Escape dismisses dialog",
      );
      await page.screenshot({
        path: `/tmp/jarvis-dashboard-${viewport.width}.png`,
        fullPage: true,
      });
      rejectFiles = true;
      await page.locator("#refresh").click();
      await page.waitForFunction(
        () => document.getElementById("file-count").textContent === "--",
      );
      check(
        (await page.locator("#files li").count()) === 0,
        "403 removes stale scoped files",
      );
      rejectFiles = false;
      expired = true;
      await page.locator("#refresh").click();
      await page.waitForFunction(
        () => !document.getElementById("login").hidden,
      );
      check(
        await page.locator("#profile").isDisabled(),
        "Expired login disables profile switching",
      );
      check(
        (await page.locator("#files li").count()) === 0,
        "Expired login clears files",
      );
      check(
        (await page.locator("#model").textContent()) === "Nicht geladen",
        "Expired login clears model",
      );
      check(
        !requests.some((path) =>
          /telemetry|license|knowledge\/files/.test(path),
        ),
        "No telemetry, license or unscoped knowledge API calls",
      );
      check(errors.length === 0, `No JS runtime errors: ${errors.join(", ")}`);
      check(
        brokenAssets.length === 0,
        `All local assets load: ${brokenAssets.join(", ")}`,
      );
      await context.close();
    }
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    let apiRequests = 0;
    page.on("request", (request) => {
      if (request.url().includes("/api/")) apiRequests++;
    });
    await page.goto(`${base}/dashboard`);
    await page.waitForFunction(() => !document.getElementById("login").hidden);
    check(apiRequests === 0, "No protected requests without a token");
    check(
      (await page.locator("#pause").getAttribute("aria-pressed")) === "true",
      "Reduced motion preference respected",
    );
    await page.locator("#open-chat").click();
    check(
      await page.locator("#chat-dialog").isHidden(),
      "Chat not opened without authentication",
    );
    await context.close();
    console.log(
      `Dashboard: ${assertions} assertions passed; desktop/mobile screenshots in /tmp.`,
    );
  } finally {
    await browser.close();
  }
}
run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
