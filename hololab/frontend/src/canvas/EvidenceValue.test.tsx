import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';
import {EvidenceValue} from './EvidenceValue';

describe('EvidenceValue', () => {
  const value = '/cloud/cloud-ssd1/a-very-long-workspace-directory/packs/community/colmap-sfm@0.1.0/manifest.yaml';

  it('keeps long values collapsed by default', () => {
    const html = renderToStaticMarkup(<EvidenceValue value={value} />);
    expect(html).toContain('<details');
    expect(html).not.toContain('hl-evidence-full');
  });

  it('can render a complete selectable path without a disclosure', () => {
    const html = renderToStaticMarkup(<EvidenceValue value={value} truncate={false} />);
    expect(html).toContain('hl-evidence-full');
    expect(html).toContain(value);
    expect(html).not.toContain('<details');
  });
});
