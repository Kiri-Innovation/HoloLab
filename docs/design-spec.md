# HoloLab editor design language

## Intent

HoloLab is a compact technical editor, not a marketing dashboard. Its visual
character takes the useful parts of Unity Editor and Figma: quiet neutral
surfaces, clear containment, predictable controls and one restrained blue
accent. The canvas remains visually subordinate to work placed on it. No
gradients, glow effects, ornamental shadows, or colour used solely for
decoration.

## Foundations

`hololab/frontend/src/theme/tokens.css` is the sole source of visual values.
Components use semantic `var(--…)` values, never colour literals. The two
theme blocks define the same semantic set.

| Foundation | Tokens / rule |
| --- | --- |
| Layers | `--canvas` is the work area; `--surface` is a docked panel; `--overlay` / `--surface-raised` are menus and drawers. `--shadow-1` is local separation and `--shadow-2` is overlays only. |
| Space | 4px baseline: `--space-1` through `--space-9` = 4, 8, 12, 16, 20, 24, 32, 40, 48px. Use 8/12 inside dense controls and 16/24 between groups. |
| Shape | `--radius-sm` 4px controls, `--radius-md` 6px cards/nodes, `--radius-lg` 10px large previews. Pill is reserved for count and state badges. |
| Type | Inter/system UI; 10/11/12/13/15/18/24px ladder (`--fs-*`), regular body, medium labels, semibold headings/actions. Body uses `--lh-normal`; controls use `--lh-ui`. |
| Control heights | `--control-h-sm`: 24px for dense table-row/icon actions; `--control-h-md`: 28px for all standard toolbar, panel, input, select and Refresh controls (including Run in a header); `--control-h-lg`: 32px for primary page actions and drawer Apply. Adjacent controls share a height. Control text is `--fs-sm`. |
| Borders | `--border-subtle` for internal structure, `--border` default containment, `--border-strong` interactive/selected-neutral edge, `--border-focus` focus. |
| Motion | `--dur-fast` 150ms and `--dur-normal` 200ms with `--ease`. Motion is limited to colour, border, shadow and small positional feedback. |

### Theme references

Dark mode follows the neutral VS Code Dark Modern gray scale, rather than a
blue-black application palette: `#181818` canvas/base, `#1f1f1f` docked
panels, `#252526` raised controls and cards, and `#2d2d2d` hover. Text steps
are `#cccccc`, `#9d9d9d`, and `#6e6e6e`; structure uses `#3c3c3c` borders and
`#2b2b2b` separators. Blue `#0078d4` remains intentional and sparse—for
selection, focus and primary actions only. Light mode mirrors this with
neutral Linear/Figma-like layers, never tinted gray. Status fills use their
semantic soft tokens at low opacity, so they communicate state without
becoming decorative panels.

## Components and interaction

- Docked panels use a surface and one shared divider. Their header uses the
  `hl-panel-header` treatment: 10px uppercase label, modest tracking and
  actions aligned to the trailing edge.
- Every standard input, select and button has a 28px `--control-h-md`.
  Use 24px `--control-h-sm` only for dense node/row icon actions; drawer
  Apply and primary page actions use 32px `--control-h-lg`.
- Buttons are three-tier: `hl-button--primary` is the single consequential
  action in a locality, `--secondary` is contained but neutral, and `--ghost`
  is low-emphasis/icon utility. Disabled controls lose emphasis but preserve
  readable text; all focusable controls use `--focus-ring` on `:focus-visible`.
- Rows and cards indicate hover using `--surface-hover` and a border change,
  not a saturated fill. Tables maintain a subtle row divider and clear hover.
- Native `title` remains the accessible tooltip fallback. `data-tooltip`
  applies the common dark, compact tooltip treatment when inline instruction
  is needed.
- Icons are monochrome, 14px by default (`--icon-size`), inherit text colour,
  and never gain standalone decorative colour. Status dots/badges are the
  only exception.

## Semantic colour

Accent is for selection, focus, links and primary actions. Statuses are
`success`, `warning`, `error`, `info`, and `neutral`, each with a `-soft`
background. Workflow states map through `--status-*`, rather than owning
their own palette. Preview and snapshot chrome use inverse semantic tokens
(`--inverse-*`), so they are valid in both themes. The light and dark body
text tokens have been chosen for at least 4.5:1 contrast against their normal
surface; muted copy is limited to metadata, never essential instructions.

## VS Code dark theme mapping

Dark mode is intentionally based on VS Code Dark+/Dark Modern rather than a
blue-black interpretation of an editor. Neutral greys have no blue or indigo
cast. The values below are literal mappings in `tokens.css`; light mode retains
the parallel semantic names, so a component never needs a theme branch.

| HoloLab semantic token | VS Code reference | Value |
| --- | --- | --- |
| `--canvas` | editor background | `#1e1e1e` |
| `--bg` | activity/sidebar base | `#181818` |
| `--surface` | workbench panel | `#1f1f1f` |
| `--surface-2` | editor group/card | `#252526` |
| `--surface-raised` / `--surface-alt` | active elevated control | `#2d2d30` |
| `--border-subtle` / `--border` | separator / widget border | `#2b2b2b` / `#3c3c3c` |
| `--text` / `--text-muted` / `--text-subtle` | editor foreground tiers | `#cccccc` / `#8b8b8b` / `#6a6a6a` |
| `--accent` / hover | VS Code blue | `#0078d4` / `#026ec1` |
| `--selection` | editor selection | `#264f78` |
| `--info`, `--success`, `--warning`, `--error` | VS Code status family | `#3794ff`, `#4ec9b0`, `#e2c08d`, `#f48771` |

## Control height scale

Controls use exactly three heights. `--control-h-sm` is 24px for dense table
row and icon actions; `--control-h-md` is 28px for ordinary toolbar, panel,
form, select, filter, search, Refresh, and header Run controls;
`--control-h-lg` is 32px for primary page actions and drawer Apply. Inputs and adjacent action
buttons use the same token. Panel-header controls are always md. No component
introduces its own resolved control height, and control text remains `--fs-sm`.

## Header control & chip spec

Workflow, page, and panel headers use one compact family. Interactive controls
(Save, Run, back/navigation, theme choices, inputs, and Refresh) are exactly
28px (`--control-h-md`), use `--radius-sm` (4px), a 1px
`--border-strong` edge, `--surface` fill, 12px horizontal padding, and
`--fs-sm`/medium labels. Primary controls retain those
dimensions and substitute the accent fill and edge. The theme selector is a
row of individual 28px square controls so its buttons share that geometry.

Read-only metadata and count summaries are chips, not controls: exactly 24px
(`--control-h-sm`), pill radius, 1px `--border` edge, `--surface-2` fill,
8px horizontal padding, and `--fs-micro` text. IDs add mono type; count chips
add tabular figures. Header rows use flex centering; dividers and status/message
slots are 24px high so they share the same vertical axis as the chips.

## Round-two workstation rules

- Body copy is 13px. Headers are 11px uppercase labels with tracking; IDs,
  timestamps, sizes and progress use `--font-mono` with tabular figures.
- Dense dock icon controls are 24px; standard dock controls are 28px and
  consequential actions are 32px. Dock headers are exactly 32px and follow
  an 8px internal rhythm. No off-scale spacing.
- Canvas, docked panel and raised card must remain visibly distinct. Canvas
  dots use `--rf-grid`; cards and nodes use a crisp border plus tight shadow.
- Accent blue is reserved for primary actions, active/selected state, focus,
  links and running state. The selected graph node is the sole permitted glow.
- Hover/press feedback is 140/160ms ease-out. Graph position and edges never
  animate, avoiding motion while arranging a workflow.

## Application checklist

- Gallery and Artifact cards/tables: panel surface, compact header, row/card
  hover, semantic status badges and common control height.
- Canvas: `--canvas` background; ReactFlow nodes retain dense layout but use
  shared radius, borders, shadows and status tokens.
- Packs, compute, runs and inspector: docked panel header rhythm and 4px
  spacing scale. Forms rely on the global input/select/textarea baseline.
- Preview and node settings drawers: overlay/inverse layers, no literal
  colours, and the same focus, button and tooltip behavior as the rest of
  the editor.

## Resizable workspace regions

The packs dock, compute dock, inspector strip, and their internal panel splits
are user-resizable. A quiet 6px edge hit area surrounds a 1px divider: it uses
the appropriate column/row resize cursor and thickens subtly on hover or drag.
Dragging is pointer-event based and updates only layout measurements; it never
changes workflow or job state. Double-clicking a divider restores that region's
default size. Measurements are clamped so every dock remains usable and are
saved locally under the `hl-layout-*` keys. Dock contents scroll within their
own regions rather than expanding the application shell.
