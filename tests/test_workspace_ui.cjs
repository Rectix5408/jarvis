// SPDX-License-Identifier: Apache-2.0
// Visual fixtures exercise actual page markup/styles; no production mock data is added.
const assert = require("node:assert/strict");
const { createRequire } = require("node:module");
const dependency = process.env.DASHBOARD_NODE_MODULES
  ? createRequire(`${process.env.DASHBOARD_NODE_MODULES}/package.json`)
  : require;
const { chromium } = dependency("playwright");
const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8767";
let checks = 0;
function check(condition, message) {
  assert.ok(condition, message);
  checks++;
}
async function run() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 390, 320]) {
      const context = await browser.newContext({
        viewport: { width, height: 960 },
      });
      await context.addInitScript(() =>
        localStorage.setItem("jarvis_token", "visual-test-only"),
      );
      await context.route("**/api/**", (route) =>
        route.fulfill({
          json:
            new URL(route.request().url()).pathname === "/api/me"
              ? { username: "Rene", is_admin: false, permissions: {} }
              : {
                  ok: true,
                  status: "ok",
                  enabled: false,
                  groups: [],
                  unread: 0,
                },
        }),
      );
      // Legacy integrations are outside this CSS-focused visual test.
      await context.route("**/*", (route) =>
        route.request().resourceType() === "script" ||
        new URL(route.request().url()).origin !== base
          ? route.abort()
          : route.fallback(),
      );
      const page = await context.newPage();
      for (const route of ["/portal", "/settings", "/chat", "/wissen"]) {
        await page.goto(base + route);
        await page.evaluate((route) => {
          if (route === "/settings") {
            document.getElementById("login-screen").classList.remove("active");
            document.getElementById("settings-modal").classList.add("open");
          }
          if (route === "/chat") {
            document.getElementById("login-screen").classList.add("hidden");
            document.getElementById("chat-screen").classList.remove("hidden");
            document.getElementById("msg-input").value =
              "Ordne meine Aufgaben fuer heute.";
          }
          if (route === "/wissen") {
            document.getElementById("wi-app").classList.remove("hidden");
            document.getElementById("wi-scope-banner").textContent =
              "Wissensgruppen";
          }
        }, route);
        await page.waitForTimeout(450);
        if (
          await page.evaluate(
            () => document.documentElement.scrollWidth > innerWidth,
          )
        ) {
          console.log(
            "Overflow",
            route,
            width,
            await page.evaluate(() =>
              [...document.querySelectorAll("body *")]
                .map((el) => ({
                  id: el.id,
                  cls: el.className,
                  right: el.getBoundingClientRect().right,
                  width: el.getBoundingClientRect().width,
                }))
                .filter((el) => el.width && el.right > innerWidth + 1)
                .slice(0, 20),
            ),
          );
          await page.screenshot({
            path: "/tmp/jarvis-workspace-overflow.png",
            fullPage: true,
          });
        }
        check(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          `${route}: no viewport overflow at ${width}`,
        );
        check(
          await page.evaluate(
            () =>
              getComputedStyle(document.body)
                .getPropertyValue("--accent")
                .trim() === "#49b6a8",
          ),
          `${route}: shared accent`,
        );
        if (route === "/settings") {
          check(
            await page.locator("#settings-modal").isVisible(),
            "Settings modal visible",
          );
          check(
            await page
              .locator("#settings-modal .modal-content")
              .evaluate((el) => getComputedStyle(el).display === "grid"),
            "Settings workspace grid",
          );
          const tabs = await page
            .locator("#settings-modal .settings-tabs")
            .boundingBox();
          const body = await page
            .locator("#settings-modal .modal-body")
            .boundingBox();
          check(
            width > 760
              ? tabs.x + tabs.width <= body.x + 1
              : tabs.y + tabs.height <= body.y + 1,
            "Navigation does not overlap form",
          );
          check(
            (await page.locator("[data-settings-tab=security]").count()) > 0 &&
              (await page.locator("[data-settings-tab=cron]").count()) > 0,
            "Security and task controls retained",
          );
        }
        if (route === "/portal") {
          check(
            new URL(page.url()).pathname === "/portal",
            "Portal fixture stays on authenticated page",
          );
          check(
            await page.locator('.pt-card[href="/dashboard"]').isVisible(),
            "Dashboard entry visible",
          );
          check(
            await page.locator("#pt-settings").isHidden(),
            "Admin controls remain hidden for normal user",
          );
        }
        if (route === "/chat") {
          await page.evaluate(() => {
            if (innerWidth <= 760)
              document
                .getElementById("chat-screen")
                .classList.add("sidebar-collapsed");
          });
          if (width <= 760)
            check(
              await page.locator("#chat-sidebar").isHidden(),
              "Collapsed mobile history stays hidden",
            );
          check(
            await page.locator("#msg-input").isVisible(),
            "Chat composer visible",
          );
          check(
            await page
              .locator("#msg-input")
              .evaluate(
                (el) => el.getBoundingClientRect().bottom <= innerHeight + 1,
              ),
            "Composer remains in viewport",
          );
        }
        await page.screenshot({
          path: `/tmp/jarvis-workspace-${route.slice(1)}-${width}.png`,
          fullPage: true,
        });
        await page.evaluate(() => document.body.classList.add("light"));
        await page.waitForTimeout(450);
        check(
          await page.evaluate(
            () => getComputedStyle(document.body).color === "rgb(26, 34, 51)",
          ),
          `Light theme keeps readable text: ${route}, ${width}, ${await page.evaluate(() => getComputedStyle(document.body).color)}`,
        );
        if (width === 1440)
          check(
            route !== "/settings" ||
              (await page
                .locator("#settings-modal .settings-tab-btn.active")
                .evaluate(
                  (el) => getComputedStyle(el).color === "rgb(38, 121, 111)",
                )),
            "Light theme active navigation has dark accent",
          );
        if (width === 1440)
          await page.screenshot({
            path: `/tmp/jarvis-workspace-${route.slice(1)}-light.png`,
            fullPage: true,
          });
      }
      await page.goto(base + "/settings");
      check(
        (await page.locator("#login-username").isVisible()) &&
          (await page.locator("#login-password").isVisible()),
        "Login controls retained",
      );
      check(
        (await page.locator("#login-username").getAttribute("autocomplete")) ===
          "username",
        "Password manager integration retained",
      );
      await page.screenshot({
        path: `/tmp/jarvis-workspace-login-${width}.png`,
        fullPage: true,
      });
      // Branding remains authoritative over the new default palette.
      await page.evaluate(() =>
        document.body.style.setProperty("--accent", "#cc4455"),
      );
      await page.waitForTimeout(450);
      check(
        await page
          .locator(".btn-login")
          .evaluate(
            (el) => getComputedStyle(el).backgroundColor === "rgb(204, 68, 85)",
          ),
        "Custom brand color respected",
      );
      await context.close();
    }
    console.log(`Workspace: ${checks} layout/theme assertions passed.`);
  } finally {
    await browser.close();
  }
}
run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
