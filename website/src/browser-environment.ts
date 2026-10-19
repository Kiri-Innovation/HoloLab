// Match the product reference action: hololab/frontend/src/flops.ts.
// CoBrowser injects its bridge before page scripts; run before React mounts.
const bridge = (window as Window & {
  flops?: { version?: unknown; insertReference?: unknown };
}).flops;
document.documentElement.dataset.flops =
  bridge?.version === 1 && typeof bridge.insertReference === 'function' ? '1' : '0';
export {};
