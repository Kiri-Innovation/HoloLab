import { packageKey } from '../packageIdentity';

/** Canonical key shared by central-status requests and response lookups. */
export const centralStatusKey = (name: string, version: string) => packageKey(name, version);
