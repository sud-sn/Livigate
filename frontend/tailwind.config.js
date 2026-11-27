/** Tailwind reads every colour, radius and duration from the design tokens in src/styles/tokens.css. */
const v = (name) => `rgb(var(--lv-${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        teal: { DEFAULT: v("teal"), hover: v("teal-hover"), press: v("teal-press"), soft: v("teal-soft") },
        ink: v("ink"),
        mint: { DEFAULT: v("mint"), stroke: v("mint-stroke") },
        mist: v("mist"),
        surface: { DEFAULT: v("surface"), raised: v("surface-raised") },
        line: v("line"),
        muted: v("muted"),
        subtle: v("subtle"),
        skeleton: v("skeleton"),
        success: { DEFAULT: v("success"), soft: v("success-soft") },
        attention: { DEFAULT: v("attention"), soft: v("attention-soft") },
        rejected: { DEFAULT: v("rejected"), soft: v("rejected-soft") },
        info: { DEFAULT: v("info"), soft: v("info-soft") },
        chart: { DEFAULT: v("chart"), grid: v("chart-grid") },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ['"Space Grotesk"', "Inter", "sans-serif"],
      },
      borderRadius: { sm: "6px", DEFAULT: "8px", md: "10px", lg: "16px" },
      transitionDuration: { press: "120ms", hover: "150ms", panel: "250ms", success: "350ms" },
      transitionTimingFunction: { out: "cubic-bezier(.2,0,0,1)", standard: "cubic-bezier(.4,0,.2,1)" },
      maxWidth: { page: "1440px" },
    },
  },
  plugins: [],
};
