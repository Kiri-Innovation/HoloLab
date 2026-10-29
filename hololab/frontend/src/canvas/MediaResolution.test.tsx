import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MediaResolution } from "./MediaResolution";
it.each([0, -1, NaN, Infinity])('hides unloaded or invalid dimensions %s', value => {
  expect(renderToStaticMarkup(<MediaResolution width={value} height={720} kind="image" />)).toBe('');
  expect(renderToStaticMarkup(<MediaResolution width={1280} height={value} kind="video" />)).toBe('');
});
it('shares the badge and reserves button space only when requested', () => {
  const image=renderToStaticMarkup(<MediaResolution width={600} height={900} kind="image" />);
  const video=renderToStaticMarkup(<MediaResolution width={1280} height={720} kind="video" besideBackground />);
  expect(image).toContain('600 × 900');expect(image).toContain('aria-label="图片尺寸"');expect(image).not.toContain('hl-video-resolution');
  expect(video).toContain('1280 × 720');expect(video).toContain('hl-media-resolution hl-video-resolution');
});
