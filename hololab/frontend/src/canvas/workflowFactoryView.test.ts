import { expect, it } from 'vitest';
import { WORKFLOW_FACTORY_VIEW as view } from './workflowFactoryView';

it('retains the selected complete factory orbit and reconstructs its position', () => {
  expect(view.pos).toEqual([2.3381175981449394, -1.8285902945909138, -5.493436199083363]);
  expect(view.orbitTarget).toEqual([-1.2625042842387428, 0.6855000007152559, 2.1232581947170956]);
  const { yaw, pitch, orbitRadius: r, orbitTarget: t } = view;
  const reconstructed = [t[0] + Math.sin(yaw) * Math.cos(pitch) * r,
    t[1] - Math.sin(pitch) * r, t[2] + Math.cos(yaw) * Math.cos(pitch) * r];
  reconstructed.forEach((v, i) => expect(v).toBeCloseTo(view.pos[i], 10));
});
