import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { CatalogPack } from "../wire";
import { PackParameterField, matchingVariables } from "./PackParameterField";
import { parameterDifferences, parameterDifferenceText } from "./parameterDifferences";
import { WorkflowVariables } from "./WorkflowVariables";

const pack = { name: "stg-train", version: "0.4.6", params: { configpath: { type: "path" } } } as unknown as CatalogPack;
const intPack = { name: "stg-train", version: "0.4.6", params: { steps: { type: "int" } } } as unknown as CatalogPack;
const floatPack = { name: "video-trim", version: "0.1.0", params: { start_seconds: { type: "float" } } } as unknown as CatalogPack;
const variables = {dataset_dir: {type: "path" as const}, iterations: {type: "int" as const}, offset: {type: "float" as const}};

describe("workflow variable numeric editors", () => {
  it("renders integer step=1 and float step=any for both default and local values", () => {
    const html = renderToStaticMarkup(<WorkflowVariables variables={{
      iterations: {type: "int", default: 43}, offset: {type: "float", default: 43},
      dataset_dir: {type: "path", default: "/data"},
    }} values={{iterations: 12, offset: 43.5, dataset_dir: "/local"}} onChange={() => {}} />);
    for (const label of ["默认值", "本地值"]) {
      expect(html).toMatch(new RegExp(`<input aria-label="iterations ${label}" type="number" step="1"`));
      expect(html).toMatch(new RegExp(`<input aria-label="offset ${label}" type="number" step="any"`));
      expect(html).toMatch(new RegExp(`<input aria-label="dataset_dir ${label}" type="text"`));
    }
    expect(html).toContain('aria-label="offset 默认值" type="number" step="any" value="43"');
    expect(html).toContain('value="43.5"');
    expect(html).not.toContain('value="43.0"');
    expect(html).toContain('<option value="float"');
    expect(html).toContain("浮点数 · float");
  });
  it("preserves numeric-looking strings and explains values retained after a type change", () => {
    const html = renderToStaticMarkup(<WorkflowVariables variables={{offset: {type: "float", default: "43"}}}
      values={{offset: "43.5"}} onChange={() => {}} />);
    expect(html).toContain('value="43"');
    expect(html).toContain('value="43.5"');
    expect(html).toContain("请填写浮点数");
  });
});

describe("scalar variable parameter mode", () => {
  it("offers float bindings only for float parameters and displays decimal resolution", () => {
    expect(matchingVariables(variables, "float")).toEqual(["offset"]);
    expect(matchingVariables(variables, "int")).toEqual(["iterations"]);
    const html = renderToStaticMarkup(<PackParameterField pack={floatPack} params={{start_seconds: 0}}
      name="start_seconds" names={["start_seconds"]} nodeId={null} initialDirectory="/"
      variables={variables} binding="offset" effectiveValue={43.5}
      onBindingChange={() => {}} onChange={() => {}} />);
    expect(html).toContain('aria-label="start_seconds 绑定变量"');
    expect(html).toContain("→ offset");
    expect(html).toContain("43.5");
    expect(html).not.toContain("iterations");
    expect(html).not.toContain("dataset_dir");
  });
  it("requests a float variable when no matching declarations exist", () => {
    const html = renderToStaticMarkup(<PackParameterField pack={floatPack} params={{start_seconds: 0}}
      name="start_seconds" names={["start_seconds"]} nodeId={null} initialDirectory="/"
      variables={{iterations: variables.iterations}} binding="" onBindingChange={() => {}} onChange={() => {}} />);
    expect(html).toContain("请在工作流变量中添加 浮点数 变量");
    expect(html).not.toContain("→ iterations");
  });
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
