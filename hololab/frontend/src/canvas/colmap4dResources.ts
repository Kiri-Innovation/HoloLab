// Decimal MB, independent of the point-count budget owned by colmap4d.preview.
export const LOAD_CONFIRM_BYTES = 200 * 1000 * 1000;
export const imagePath = (name: string) => {
  if (!name || name.startsWith('/') || name.includes('\\') || name.split('/').some(p => p === '..' || p === '.')) throw new Error('Unsafe image path');
  return name.split('/').map(encodeURIComponent).join('/');
};
