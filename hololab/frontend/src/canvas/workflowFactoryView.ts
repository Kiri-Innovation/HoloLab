// One world-space factory camera for all workflow viewers. Keep this out of
// persisted workflow state: a missing user default must remain null.
const yaw = 2.365;
const pitch = 0.485;
const radius = 12.69;
const target = [5.33, -0.54, 4.51];
export const WORKFLOW_FACTORY_VIEW = {
  v: 2, mode: "orbit", yaw, pitch, fov: 60,
  pos: [
    target[0] + Math.sin(yaw) * Math.cos(pitch) * radius,
    target[1] - Math.sin(pitch) * radius,
    target[2] + Math.cos(yaw) * Math.cos(pitch) * radius,
  ],
  orbitTarget: target, orbitRadius: radius, up: [0, 1, 0],
};
