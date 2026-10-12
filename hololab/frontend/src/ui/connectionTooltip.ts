import type { ConnectionVerdict } from '../canvas/connectionValidation';
export interface ConnectionTooltip { target: HTMLElement; verdict: ConnectionVerdict }
export const CONNECTION_TOOLTIP_EVENT = 'hololab:connection-tooltip';
/** Separate channel: connecting intentionally shows feedback while the mouse is down. */
export function showConnectionTooltip(detail: ConnectionTooltip | null) {
  document.dispatchEvent(new CustomEvent(CONNECTION_TOOLTIP_EVENT, { detail }));
}
