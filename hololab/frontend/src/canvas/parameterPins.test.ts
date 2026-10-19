import { expect, it } from "vitest";
import { parameterPinKey, toggleParameterPins } from "./parameterPins";
import { ParameterPinButton } from "./ParameterPinButton";
it("scopes pins to exact algorithm version and preserves unknown fields", () => {
  const original = { "demo@1": ["retired"], "demo@2": ["value"] };
  const key = parameterPinKey({ name: "demo", version: "1" });
  const next = toggleParameterPins(original, key, ["value"]);
  expect(next[key]).toEqual(["retired", "value"]);
  expect(toggleParameterPins(next, key, ["value"])).toEqual(original);
  expect(original["demo@1"]).toEqual(["retired"]);
});
it("toggles a grouped transform atomically", () => {
  const pins = toggleParameterPins({ "vec@1": ["x"] }, "vec@1", ["x", "y", "scale"]);
  expect(pins["vec@1"]).toEqual(["x", "y", "scale"]);
  expect(toggleParameterPins(pins, "vec@1", ["x", "y", "scale"])).toEqual({});
});
it("announces workflow scope and uses filled plus pressed state", () => {
  const button = ParameterPinButton({ names: ["count"], algorithmKey: "demo@1", pinned: ["count"], onToggle: () => {} });
  expect(button.props["aria-pressed"]).toBe(true);
  expect(button.props["aria-label"]).toContain("all demo@1 nodes in this workflow");
  expect(button.props.children.props.fill).toBe("currentColor");
});
