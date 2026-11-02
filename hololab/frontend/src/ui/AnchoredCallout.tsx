import {useLayoutEffect, useRef, type CSSProperties, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {tooltipGeometry} from './tooltipGeometry';

/** Like TooltipLayer: escape clipping ancestors and place in screen coordinates. */
export function AnchoredCallout({children, align = 'end', style, ...attributes}: {
  children: ReactNode;
  align?: 'start' | 'end';
  style?: CSSProperties;
  role: 'status' | 'alert';
  'data-hl-open-cocoder-guide'?: string;
  'data-hl-source-error'?: string;
}) {
  const marker = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const anchor = marker.current?.parentElement;
    const element = bubble.current;
    if (!anchor || !element) return;
    const update = () => {
      const rect = anchor.getBoundingClientRect();
      const size = element.getBoundingClientRect();
      const position = tooltipGeometry(
        {...rect.toJSON(), left: align === 'start' ? rect.left : rect.right - size.width},
        size, {width: window.innerWidth, height: window.innerHeight}, 1,
        {align: 'left', placement: 'below', gap: 6},
      );
      element.style.left = `${position.left}px`;
      element.style.top = `${position.top}px`;
    };
    update();
    const sizes = new ResizeObserver(update);
    sizes.observe(anchor);
    sizes.observe(element);
    window.addEventListener('resize', update);
    document.addEventListener('scroll', update, true);
    return () => {
      sizes.disconnect();
      window.removeEventListener('resize', update);
      document.removeEventListener('scroll', update, true);
    };
  }, [align]);
  return <><span ref={marker} hidden />{createPortal(<div ref={bubble} {...attributes} style={{
    ...style, position: 'fixed', zIndex: 12000, boxSizing: 'border-box',
    maxWidth: 'calc(100vw - 16px)', maxHeight: 'calc(100dvh - 16px)',
    overflow: 'auto', overflowWrap: 'anywhere',
  }}>{children}</div>, document.body)}</>;
}
