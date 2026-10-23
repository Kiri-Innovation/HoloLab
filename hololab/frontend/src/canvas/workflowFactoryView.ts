// One world-space factory camera for all workflow viewers. Keep this out of
// persisted workflow state: a missing user default must remain null.
// User-selected complete orbit view from ca5d26f1 (2026-10-23).
// Target and radius are part of the view: scene-specific fitting would change
// the requested camera and make different viewer types disagree again.
export const WORKFLOW_FACTORY_VIEW = {
  v: 2,
  mode: "orbit",
  pos: [2.3381175981449394, -1.8285902945909138, -5.493436199083363],
  up: [0, 1, 0],
  yaw: 2.7000000000000006,
  pitch: 0.28999999999999915,
  fov: 60,
  orbitTarget: [-1.2625042842387428, 0.6855000007152559, 2.1232581947170956],
  orbitRadius: 8.791994167502853,
};
