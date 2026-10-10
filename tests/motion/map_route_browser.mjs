// Loads an installed map-route block in headless Chrome, once per variable set, and reports what the page did.
// usage: node map_route_browser.mjs PROJECT_DIR CHROME PUPPETEER_CORE < sets.json   (sets: {name: {variables}})
// Used only by the opt-in browser test in test_map_route.py; it makes no request outside the project folder.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [project, chrome, puppeteerPath] = process.argv.slice(2);
const sets = JSON.parse(fs.readFileSync(0, "utf8"));
const puppeteer = (await import(pathToFileURL(puppeteerPath).href)).default;
fs.copyFileSync(path.join(project, "compositions/map-route.html"), path.join(project, "standalone.html"));

const browser = await puppeteer.launch({
  executablePath: chrome,
  args: ["--no-sandbox", "--allow-file-access-from-files", "--font-render-hinting=none"],
  defaultViewport: { width: 1920, height: 1080 },
});
const report = {};
for (const [name, variables] of Object.entries(sets)) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.evaluateOnNewDocument((vars) => {
    window.__hfVariables = vars;
  }, variables);
  await page.goto(pathToFileURL(path.join(project, "standalone.html")).href);
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => {
    const tl = window.__timelines && window.__timelines["map-route"];
    if (!tl) return null;
    const boxes = () => {
      const rect = (el) => {
        const r = el.getBoundingClientRect();
        return { x0: r.left, y0: r.top, x1: r.right, y1: r.bottom };
      };
      const visible = (el) => Number(getComputedStyle(el).opacity) > 0.99 && getComputedStyle(el).display !== "none";
      const pick = (selector) =>
        [...document.querySelectorAll(selector)].filter(visible).map((el) => ({
          text: el.textContent,
          size: Number.parseFloat(getComputedStyle(el).fontSize),
          font: getComputedStyle(el).fontFamily,
          box: rect(el),
        }));
      return {
        labels: pick(".pin-label"),
        corner: pick(".year, .caption"),
        dots: [...document.querySelectorAll(".pin-dot")].filter(visible).map((el) => rect(el)),
      };
    };
    const duration = tl.duration();
    tl.pause();
    tl.time(duration - 0.6, false);
    const held = boxes();
    tl.time(0, false);
    const stageAtStart = Number(getComputedStyle(document.querySelector(".stage")).opacity);
    tl.time(duration, false);
    const stageAtEnd = Number(getComputedStyle(document.querySelector(".stage")).opacity);
    return { duration, held, stageAtStart, stageAtEnd, fontsLoaded: document.fonts.check('52px "IBM Plex Sans Condensed"') };
  });
  report[name] = { errors, result };
  await page.close();
}
await browser.close();
process.stdout.write(JSON.stringify(report));
