/** Connection completeness only; type compatibility and drift keep their own rules. */
export function missingRequiredInputs(inputs: Record<string, { required: boolean }>, connected: (string | null | undefined)[]): string[] {
  const wired = new Set(connected);
  return Object.entries(inputs).filter(([name, spec]) => spec.required && !wired.has(name)).map(([name]) => name);
}
