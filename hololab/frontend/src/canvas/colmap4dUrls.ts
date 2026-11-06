/** Append only relative members to the handle's authoritative same-origin proxy_url. */
export function colmap4dUrl(proxyUrl: string, member = ''): string {
  if (!proxyUrl.startsWith('/proxy/')) throw new Error('Expected handle proxy_url');
  const parts = `${proxyUrl}/${member}`.split('/').filter(part => part && part !== '.');
  if (parts.some(part => part === '..')) throw new Error('Invalid preview member');
  return '/' + parts.join('/');
}
