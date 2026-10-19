export function attachCodeCopy(labels: { copy: string; copied: string; failed: string }) {
  const cleanups: (() => void)[] = [];
  document.querySelectorAll<HTMLElement>('.prose .copyable-code').forEach(block => {
    const code = block.querySelector('pre code');
    if (!code) return;
    const bar = document.createElement('div');
    bar.className = 'code-copy-bar';
    const status = document.createElement('span');
    status.setAttribute('role', 'status');
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = labels.copy;
    bar.append(status, button); block.prepend(bar);
    let active = true;
    button.onclick = async () => {
      const text = code.textContent ?? '';
      let copied = false;
      try {
        if (window.isSecureContext && navigator.clipboard) {
          await navigator.clipboard.writeText(text); copied = true;
        }
      } catch { /* HTTP / denied clipboard access uses the fallback below. */ }
      if (!active) return;
      if (!copied) {
        const input = document.createElement('textarea');
        input.value = text; input.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
        document.body.append(input); input.select();
        try { copied = document.execCommand('copy'); } catch { /* Show manual-copy guidance. */ }
        input.remove(); button.focus({ preventScroll: true });
      }
      status.textContent = copied ? labels.copied : labels.failed;
    };
    cleanups.push(() => { active = false; bar.remove(); });
  });
  return () => cleanups.forEach(cleanup => cleanup());
}
