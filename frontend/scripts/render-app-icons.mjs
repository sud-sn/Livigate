// Renders the PNG app icons (512 and 192 px) for the web manifest from the brand SVG. Run once after changing the mark.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// Playwright is provided by the environment (not a project dependency), so resolve it the CommonJS way.
const { chromium } = createRequire(import.meta.url)("playwright");

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const svg = readFileSync(resolve(root, "public/brand/levigate-icon.svg"), "utf8");
const browser = await chromium.launch();
for (const size of [512, 192]) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg.replace(/width="512" height="512"/, `width="${size}" height="${size}"`)}</body></html>`);
  await page.locator("svg").screenshot({ path: resolve(root, `public/brand/levigate-icon-${size}.png`), omitBackground: true });
  await page.close();
}
await browser.close();
console.log("rendered 512 and 192 px icons");
