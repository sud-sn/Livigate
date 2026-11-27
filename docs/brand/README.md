# Levigate brand guide

**Levigate**, by GainInsights — *Every script, smoothed to paid.*

Alternate taglines: *Rough edges in. Revenue out.* · *Smooth operations. Proven compliance.* · *One smooth flow, from script to paid.*

The source files live in [`frontend/public/brand/`](../../frontend/public/brand/). The product UI reads every value
below from the design tokens in [`frontend/src/styles/tokens.css`](../../frontend/src/styles/tokens.css); change a
value there, not in a component.

## The mark

The prescription sign, ℞, drawn as one smooth stroke: the R's leg sweeps out into a level base, the way levigation
works a powder into an even preparation. The mint cross-stroke makes it read Rx at a glance and is reused as the
accent for progress and success.

| File | Use |
| --- | --- |
| `levigate-icon.svg` | App icon, 512 / 192 / 96 / 48 px (6 px strokes on a 64 grid) |
| `levigate-favicon.svg` | 32 and 16 px favicons — heavier 8 px strokes so the mark stays crisp |
| `levigate-icon-on-dark.svg` | Icon on Ink backgrounds (tile lifts to `#0E6B6E`) |
| `levigate-lockup.svg` | Icon + `levigate` wordmark (Space Grotesk 600, lower case) |
| `levigate-icon-512.png`, `levigate-icon-192.png` | Raster app icons for the web manifest |

Clear space: at least a quarter of the icon's width on every side. Before public use: trademark search for
"Levigate" (software and healthcare classes) and a domain check.

## Colour

| Token | Hex | Use |
| --- | --- | --- |
| Deep Teal | `#0B4F57` | Primary: buttons, links, icons, the icon tile |
| Ink | `#0B2E33` | Text; dark surfaces |
| Mint | `#5EEAD4` | Accent on dark or as a stroke only (focus ring, progress, success stroke) — never text on light |
| Mist | `#F4F9F8` | App background |
| Success | `#0F7A5A` | Paid / approved — always with an icon and a word |
| Attention | `#B54708` | Pending / due soon — always with an icon and a word |
| Rejected | `#B42318` | Rejected / failing — always with an icon and a word |
| Info | `#175CD3` | Informational notices |

Supporting neutrals from the brand sheet: border `#D6E4E2`, muted text `#3C5257` / `#4A6266`, skeleton `#E3EEEC`.

## Type

Space Grotesk 600 for the wordmark and headings; Inter 400–600 for interface text. Both are self-hosted from the
app bundle (no third-party font requests, which keeps the CSP tight).

## Motion and feedback

Every click answers within 100 ms and every wait shows real progress; motion explains, it never decorates.
CSS transitions and keyframes only, from one set of motion tokens.

| Moment | Behaviour | Timing |
| --- | --- | --- |
| App start | The Rx draws itself and the mint stroke lands; first load only | 2.4 s loop, `cubic-bezier(.2,0,0,1)` |
| Lists and tables | Skeleton rows in the final layout, so nothing jumps | Until loaded |
| Short waits | Spinner inside the pressed button, with a verb ("Saving and logging…") | Only after 300 ms |
| Long jobs (> 2 s) | Named steps with checks ("Connected → Rules applied → Building work items") | Live |
| Button press | Scale to 97 %, colour shift | 120 ms |
| Hover and focus | Colour change; mint focus ring for keyboard users | 150 ms |
| Panels and toasts | Rise 8 px and fade in, ease-out | 250 ms |
| Success | Check mark draws, then the audit line appears beneath | 350 ms |
| Approvals | Never optimistic: success shows only after the server confirms and logs it | Server-driven |
| Reduced motion | All animation off when the OS asks | Always |

The approve-a-claim sequence: **Approve resubmission** → button shows a spinner and "Saving and logging…" → the
server records the approval and audit entry → a success chip draws its check ("Approved · resubmission set for
Dec 12") → the audit line rises in beneath ("Audit trail: approved by J. Patel, rule 79 v3, 10:42 AM").

## Iconography

[Lucide](https://lucide.dev) (ISC licence) is the base set; the pharmacy icons below are drawn to the same rules and
ship as React components and an SVG sprite from the design system (`frontend/src/design-system/icons`). No icon
fonts, no emoji.

| Rule | Specification |
| --- | --- |
| Grid and stroke | 24 px grid, 1.75 px stroke, round caps and joins, no fills except status dots |
| Sizes | 16 px in tables, 20 px in buttons and navigation, 24–28 px in empty states |
| Custom icons | Prescription, medication, compounding, claim, rejected claim, payer, prescriber, patient, call, refill, consent, audit log, compliance, AI draft, approve, alert |
| Labels | Action icons sit beside a word; icon-only buttons carry an accessible name and a tooltip |
| Status | Never colour alone: paid, pending and rejected each pair an icon, a colour and a word |
| Accent strokes | Mint (`#0FA38A` on light) for success and progress, red for rejection, amber for attention; everything else teal |
| AI marker | Every AI-drafted text carries the AI draft icon and the label "AI draft" until a person edits or approves it |
