/**
 * Levigate pharmacy icons: 24 px grid, 1.75 px stroke, round caps and joins, no fills (brand iconography rules).
 * Base strokes use currentColor; accent strokes use the status/mint tokens so meaning survives theming.
 * The same JSON feeds the SVG sprite (scripts/build-icon-sprite.mjs).
 */
import type { SVGProps } from "react";
import defs from "./pharmacy-icons.json";

type Accent = "mint" | "rejected" | "attention";
type IconDef = {
  label: string;
  paths: { d: string; accent?: Accent }[];
  circles?: { cx: number; cy: number; r: number }[];
};

export type PharmacyIconName = keyof typeof defs;

const ACCENT: Record<Accent, string> = {
  mint: "rgb(var(--lv-mint-stroke))",
  rejected: "rgb(var(--lv-rejected))",
  attention: "rgb(var(--lv-attention))",
};

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "name"> {
  name: PharmacyIconName;
  size?: 16 | 20 | 24 | 28;
  /** Accessible name. Omit for decorative icons that sit beside a word. */
  title?: string;
}

export function PharmacyIcon({ name, size = 20, title, ...rest }: IconProps) {
  const def = (defs as Record<string, IconDef>)[name];
  const strokeWidth = size <= 16 ? 2 : 1.75; // heavier at table size so it stays crisp
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
      {...rest}
    >
      {def.circles?.map((c) => <circle key={`${c.cx}-${c.cy}`} cx={c.cx} cy={c.cy} r={c.r} />)}
      {def.paths.map((p) => (
        <path key={p.d} d={p.d} stroke={p.accent ? ACCENT[p.accent] : undefined} />
      ))}
    </svg>
  );
}

export const PHARMACY_ICON_NAMES = Object.keys(defs) as PharmacyIconName[];
export const pharmacyIconLabel = (name: PharmacyIconName) => (defs as Record<string, IconDef>)[name].label;
