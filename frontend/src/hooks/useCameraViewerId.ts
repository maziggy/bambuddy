import { useState } from 'react';

/**
 * A random id for one camera viewer, stable while it is mounted. It goes on
 * the viewer's stream URL and on its /camera/stop. Every camera, external ones
 * included, now shares one upstream per printer, and the stop leaves it running
 * while another viewer still watches; the id names the viewer in the request.
 *
 * Math.random, not crypto.randomUUID: that needs a secure context, and
 * Bambuddy is often opened over plain http on the LAN.
 */
export function useCameraViewerId(): string {
  const [id] = useState(() => `v${Math.random().toString(36).slice(2, 12)}`);
  return id;
}
