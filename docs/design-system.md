# Agent Runtime — Frontend Design System Contract

> **Authority:** Persistent design contract for Agent Runtime UI. All future frontend modifications must strictly adhere to these tokens, measurements, and architectural rules. Never sacrifice spacing, typography, or interaction consistency for new features.

---

## 1. Color Palette & Surface Tokens

All surface layers adhere to an obsidian monochrome palette, with blue strictly bounded to file references and active code artifacts.

| Token | Value | Role |
|:---|:---|:---|
| `--bg-app` | `#070708` | Base dark canvas and full viewport background |
| `--bg-sidebar` | `#121216` | Persistent navigation and project tree sidebar |
| `--bg-workspace` | `#070708` | Primary agent execution workspace canvas |
| `--bg-elevated` | `#131317` | Primary elevated cards, composer background |
| `--bg-elevated-2` | `#18181e` | Second lifted layer: dropdowns, floating menus, popovers |
| `--bg-elevated-3` | `#1f1f26` | Third layer: tooltips, command palette, focus rings |
| `--bg-hover` | `rgba(255, 255, 255, 0.055)` | Standard interactive control hover state |
| `--bg-hover-strong` | `rgba(255, 255, 255, 0.095)` | Emphasized interactive control hover state |
| `--bg-active` | `rgba(255, 255, 255, 0.085)` | Generic button pressed / active state |
| `--bg-item-active` | `#1f1f24` | Active conversation item surface (Antigravity spec) |
| `--border-item-active` | `#2d2d31` | Active conversation item hairline border |
| `--border-color` | `rgba(255, 255, 255, 0.12)` | Standard hairline separator and container border |
| `--border-subtle` | `rgba(255, 255, 255, 0.08)` | Subtle nested list and inner card dividers |
| `--border-hover` | `rgba(255, 255, 255, 0.22)` | Hover boundary highlight |
| `--border-focus` | `rgba(255, 255, 255, 0.35)` | Keyboard focus ring |

### Accent & Text Contrast
| Token | Value | Role |
|:---|:---|:---|
| `--text-primary` | `#f4f4f6` | High-contrast body, titles, and active labels |
| `--text-secondary` | `#a1a1aa` | Neutral silver secondary text and descriptions |
| `--text-muted` | `#71717a` | Timestamps, counters, placeholder hints |
| `--text-placeholder` | `#52525b` | Form input placeholder text |
| `--accent-primary` | `#ffffff` | Primary monochrome CTA and icons |
| `--color-file-text` | `#60a5fa` | Strictly bounded to file paths, diffs, and project folders |

---

## 2. Typography Scale

The font stack relies on system native fonts for maximum performance and native desktop feel:
`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", "Plus Jakarta Sans", sans-serif`. Code uses `"JetBrains Mono", ui-monospace, SFMono-Regular, monospace`.

| Level | Size | Weight | Line Height | Usage |
|:---|:---|:---|:---|:---|
| Display | 26px | 600 | 1.25 | Empty-state hero title ("What would you like to work on?") |
| Section Title | 14px | 600 | 1.35 | Sidebar brand title ("Agent Runtime"), Modal headers |
| Section Header | 11.5px | 600 | 1.2 | Sidebar group titles ("Projects", uppercase or capitalized) |
| Body Regular | 13px | 400/500 | 1.5 | Conversation titles, tool messages, chat bubbles |
| Body Small | 12px | 400 | 1.4 | Descriptions, button labels, dropdown items |
| Micro / Caption | 11px | 400/500 | 1.2 | Timestamps (`10m`, `18h`), file line numbers, badges |
| Code Mono | 12.5px | 400 | 1.6 | Syntax-highlighted code editor, diffs, terminal |

---

## 3. Spacing Scale (4px Strict Grid)

All margins, paddings, and gaps must follow 4px increments:

| Token | Value | Common Usage |
|:---|:---|:---|
| `space-1` | 4px | Icon-text gap, compact button padding, menu item gaps |
| `space-2` | 8px | Container inner padding, card gaps, list item vertical margins |
| `space-3` | 12px | Sidebar horizontal padding, input padding, chip margins |
| `space-4` | 16px | Card padding, header padding, modal content padding |
| `space-5` | 20px | Major section margins, composer bottom offset |
| `space-6` | 24px | Workspace content margins, modal headers |
| `space-8` | 32px | Hero section vertical spacing |

---

## 4. Corner Radii

| Token | Value | Target |
|:---|:---|:---|
| `--radius-xs` | 4px | Badges, tiny chips, syntax token spans |
| `--radius-sm` | 6px | Conversation items, project rows, menu dropdowns |
| `--radius-md` | 10px | Cards, modals, dialog containers, composer |
| `--radius-pill`| 9999px | New Conversation button, status pills, filter chips |

---

## 5. Control Dimensions & Layout Bounds

| Element | Height / Width | Invariant |
|:---|:---|:---|
| **Sidebar Expanded** | Width: `260px` | Fixed width with smooth width animation |
| **Sidebar Collapsed** | Width: `0px` | Opacity `0`, pointer-events `none`, visibility `hidden` |
| **Header Bar** | Height: `48px` | Contains "Agent Runtime", search toggle, collapse button |
| **New Conversation Button** | Height: `36px` | Capsule pill (`border-radius: 9999px`), `#1b1b20` bg |
| **Project Folder Row** | Height: `32px` | Chevron, folder icon, blue folder title, count |
| **Conversation Row** | Height: `34px` | Indented `14px`, title truncated, relative timestamp |
| **Composer (Bottom)** | Max-Width: `800px` | Centered with responsive side margins |
| **File Viewer Drawer** | Width: `360px` – `calc(100vw - 360px)` | Resizable split-pane with drag handle |

---

## 6. Information Architecture & Project Hierarchy

### Sidebar Strict Order:
1. **Header**: `Agent Runtime` brand title + Search Icon + Collapse Sidebar Icon. No folder breadcrumbs beneath product title.
2. **Action Bar**: `+ New Conversation` capsule button.
3. **Projects Section**:
   - Header with `Projects`, Sort/Filter menu (`IconFilter`), and Add/Open Folder (`IconFolderPlus`).
   - When no project is open: Polished empty state with "Select Folder" button.
   - When projects exist: Nested project tree with blue folder headers.
   - Every conversation belongs exclusively to its project.
4. **Footer**: User profile avatar + name ("Vinayaka K") + Settings button.
5. **No Global Recent Conversations**: Removed completely to ensure strict project ownership.

---

## 7. Animation & Physics Contracts

- **Duration:** 240ms for major layout transitions, 140ms for micro-interactions (hover, active).
- **Easing Curve:** `cubic-bezier(0.16, 1, 0.3, 1)` ("Makhan" spring easing).
- **Sidebar Collapse:**
  - Transition: `width 240ms cubic-bezier(0.16, 1, 0.3, 1), opacity 200ms ease`.
  - Content wrapper fades opacity over `180ms` before width snaps to 0.
  - No abrupt `display: none` unmounting.
- **Prefers-Reduced-Motion:**
  - If `@media (prefers-reduced-motion: reduce)` is set, all transitions are reduced to `0.01ms`.

---

## 8. Accessibility Requirements
- All interactive controls have explicit `aria-label`, `role`, and `title`.
- Visible focus rings (`:focus-visible` with `2px solid var(--border-focus)` and `2px` offset).
- Keyboard shortcuts:
  - `Ctrl+B`: Toggle sidebar collapse / expand.
  - `Ctrl+K`: Toggle command palette.
  - `Ctrl+,`: Open Settings modal.
  - `Esc`: Close open modal / drawer / dropdown.
