import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { dismissToast, getToastSnapshot, showToast, subscribeToToast } from "./Toast";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { dismissToast(); vi.useRealTimers(); });

it("publishes copy feedback globally and automatically dismisses it", () => {
  const listener = vi.fn();
  const unsubscribe = subscribeToToast(listener);
  showToast({ message: "已复制引用", tone: "success" });
  expect(getToastSnapshot()?.message).toBe("已复制引用");
  expect(listener).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(3500);
  expect(getToastSnapshot()).toBeNull();
  expect(listener).toHaveBeenCalledTimes(2);
  unsubscribe();
});

it("replaces the previous notice and resets the dismissal timer", () => {
  showToast({ message: "已复制引用", tone: "success" });
  const firstId = getToastSnapshot()!.id;
  vi.advanceTimersByTime(3000);
  showToast({ message: "已复制引用", tone: "success" });
  expect(getToastSnapshot()!.id).not.toBe(firstId);
  vi.advanceTimersByTime(500);
  expect(getToastSnapshot()).not.toBeNull();
  vi.advanceTimersByTime(3000);
  expect(getToastSnapshot()).toBeNull();
});

it("keeps failure feedback longer and exposes an optional retry", async () => {
  const run = vi.fn().mockResolvedValue(undefined);
  showToast({ message: "复制失败", tone: "error", action: { label: "重试复制", run } });
  await getToastSnapshot()!.action!.run();
  expect(run).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(7999);
  expect(getToastSnapshot()?.message).toBe("复制失败");
  vi.advanceTimersByTime(1);
  expect(getToastSnapshot()).toBeNull();
});

it("supports manual dismissal and unsubscribing", () => {
  const listener = vi.fn();
  const unsubscribe = subscribeToToast(listener);
  unsubscribe();
  showToast({ message: "已复制引用", tone: "success" });
  dismissToast();
  expect(getToastSnapshot()).toBeNull();
  expect(listener).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
