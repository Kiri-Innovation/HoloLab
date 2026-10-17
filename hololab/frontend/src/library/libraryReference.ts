import { formatToken, insertOrCopyReference } from '../canvas/referenceAction';
import type { Folder, Resource } from './api';

export function libraryReference(target: Folder | Resource, folderPath: (id: string) => string) {
  const file = 'resource_id' in target;
  const kind = file ? 'library-resource' : 'library-folder';
  const id = file ? target.resource_id : target.folder_id;
  const token = formatToken(kind, id);
  const page = new URL('/library', window.location.origin);
  if (target.folder_id !== 'root') page.searchParams.set('folder', target.folder_id);
  const resolve = new URL('/api/resolve', window.location.origin);
  resolve.searchParams.set('ref', token);
  const path = folderPath(target.folder_id).replace(/\/$/, '') + (file ? '/' + target.name : '');
  const details = file
    ? `${target.resource_type}, ${target.size_bytes} bytes, ${target.status}`
    : 'folder';
  const text = [
    token,
    `Library ${file ? 'resource' : 'folder'} ${JSON.stringify(target.name)} (${details})`,
    `Library-relative path: ${JSON.stringify(path)}`,
    ...(file ? [`Placements: ${target.placements.map(p => `${p.node_name} (${p.node_id}; ${p.observed_state}; ${p.online ? 'online' : 'offline'})`).join(', ') || 'none'}`] : []),
    `Page: ${page}`,
    `Resolve: ${resolve}`,
    ...(file ? [`Resource API: ${new URL(`/api/library/resources/${id}`, page)}`] : []),
  ].join('\n');
  return {
    text,
    payload: {
      schemaVersion: 1 as const, provider: 'hololab', type: kind.replace(/-/g, '_'),
      resource: { uri: file ? new URL(`/api/library/resources/${id}`, page).toString() : page.toString() },
      display: { title: target.name, subtitle: `Library · ${details}`, icon: file ? 'document' as const : 'dataset' as const },
      access: { mode: 'link_only' as const }, snapshot: { text, mediaType: 'text/plain' as const },
    },
  };
}

export function copyLibraryReference(target: Folder | Resource, folderPath: (id: string) => string) {
  const { payload, text } = libraryReference(target, folderPath);
  return insertOrCopyReference(payload, text);
}
