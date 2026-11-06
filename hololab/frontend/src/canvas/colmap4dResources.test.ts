import {expect, test} from 'vitest';
import {imagePath, LOAD_CONFIRM_BYTES} from './colmap4dResources';
test('confirmation threshold is decimal MB', () => expect(LOAD_CONFIRM_BYTES).toBe(200000000));
test('image transport encodes nested names and refuses traversal', () => {
  expect(() => imagePath('../secret')).toThrow('Unsafe');
  expect(imagePath('cam/a #.png')).toBe('cam/a%20%23.png');
});
