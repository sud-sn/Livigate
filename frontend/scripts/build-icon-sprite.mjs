// Builds public/icons/levigate-icons.svg — an SVG sprite of the pharmacy icons — from the same JSON the React
// components use, so the two can never drift. Accent strokes use CSS custom properties with brand fallbacks.
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defs = JSON.parse(readFileSync(resolve(root, "src/design-system/icons/pharmacy-icons.json"), "utf8"));
const accent = {
  mint: "var(--lv-sprite-mint, #0FA38A)",
  rejected: "var(--lv-sprite-rejected, #B42318)",
  attention: "var(--lv-sprite-attention, #B54708)",
};
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const symbols = Object.entries(defs).map(([name, def]) => {
  const circles = (def.circles ?? []).map((c) => `<circle cx="${c.cx}" cy="${c.cy}" r="${c.r}"/>`).join("");
  const paths = def.paths
    .map((p) => `<path d="${esc(p.d)}"${p.accent ? ` stroke="${accent[p.accent]}"` : ""}/>`)
    .join("");
  return `  <symbol id="lv-${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ` +
    `stroke-linecap="round" stroke-linejoin="round"><title>${esc(def.label)}</title>${circles}${paths}</symbol>`;
});
const out = `<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n${symbols.join("\n")}\n</svg>\n`;
mkdirSync(resolve(root, "public/icons"), { recursive: true });
writeFileSync(resolve(root, "public/icons/levigate-icons.svg"), out);
console.log(`levigate-icons.svg: ${symbols.length} symbols`);
