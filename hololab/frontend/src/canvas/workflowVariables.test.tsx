import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { CatalogPack } from "../wire";
import { PackParameterField, matchingVariables } from "./PackParameterField";
import { parameterDifferences, parameterDifferenceText } from "./parameterDifferences";

const pack = { name: "stg-train", version: "0.4.6", params: { configpath: { type: "path" } } } as unknown as CatalogPack;
const intPack = { name: "stg-train", version: "0.4.6", params: { steps: { type: "int" } } } as unknown as CatalogPack;
const variables = {dataset_dir: {type: "path" as const}, iterations: {type: "int" as const}};

describe("scalar variable parameter mode", () => {
  it("filters int variables out of path choices and displays backend resolution", () => {
    expect(matchingVariables(variables, "path")).toEqual(["dataset_dir"]);
    const html = renderToStaticMarkup(<PackParameterField pack={pack} params={{configpath: "inline.json"}}
      name="configpath" names={["configpath"]} nodeId={null} initialDirectory="/"
      variables={variables} binding="dataset_dir" effectiveValue="/data/current"
      onBindingChange={() => {}} onChange={() => {}} />);
    expect(html).toContain("→ dataset_dir");
    expect(html).toContain("/data/current");
    expect(html).toContain("工作流变量解析");
    expect(html).not.toContain("iterations");
  });
  it("renders the inline editor beside a collapsed mode toggle", () => {
    const html = renderToStaticMarkup(<PackParameterField pack={intPack} params={{steps: 12}}
      name="steps" names={["steps"]} nodeId={null} initialDirectory="/"
      variables={variables} onBindingChange={() => {}} onChange={() => {}} />);
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("当前为内联值");
    expect(html).toContain('aria-label="steps"');
    expect(html).not.toContain("steps 绑定变量");
  });
  it("renders the variable picker beside an expanded mode toggle", () => {
    const html = renderToStaticMarkup(<PackParameterField pack={pack} params={{configpath: "inline.json"}}
      name="configpath" names={["configpath"]} nodeId={null} initialDirectory="/"
      variables={variables} binding="dataset_dir" onBindingChange={() => {}} onChange={() => {}} />);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('aria-label="configpath 绑定变量"');
    expect(html).toContain("当前为绑定变量");
  });
  it("switches modes without replacing the saved inline value", () => {
    const params = {configpath: "inline.json"};
    let binding: string | null = "dataset_dir";
    const field = PackParameterField({pack, params, name: "configpath", names: ["configpath"],
      nodeId: null, initialDirectory: "/", variables, binding,
      onBindingChange: next => { binding = next; }, onChange: () => { throw Error("must not change inline value"); }});
    field.props.children.props.children[0].props.onClick();
    expect(binding).toBeNull();
    expect(params.configpath).toBe("inline.json");
  });
  it("keeps the inline fallback when binding changes are unavailable", () => {
    const field = PackParameterField({pack, params: {configpath: "inline.json"}, name: "configpath", names: ["configpath"],
      nodeId: null, initialDirectory: "/", onChange: () => {}});
    expect(field.props.className).not.toBe("hl-variable-parameter");
    expect(field.props.name).toBe("configpath");
  });
  it("reuses differences with explicit variable provenance", () => {
    const diff = parameterDifferences(pack, {configpath: "inline.json"}, {configpath: "/new"}, [], {}, {configpath: "dataset_dir"})[0];
    expect(diff.reason).toBe("workflow-variable");
    expect(parameterDifferenceText(diff)).toContain("→ dataset_dir");
  });
});
