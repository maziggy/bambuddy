/**
 * The Obico status panel shows the pause level next to Low / High (#3459):
 * pausing waits for Obico's pause level (High x 1.75), so a score above High
 * alone is a warning.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { ObicoSettings } from '../../components/ObicoSettings';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({ obico_enabled: true, obico_ml_url: 'http://obico:3333' }),
    getPrinters: vi.fn().mockResolvedValue([]),
    getObicoStatus: vi.fn().mockResolvedValue({
      is_running: true,
      last_error: null,
      per_printer: {},
      thresholds: { low: 0.38, high: 0.78, pause: 1.4 },
      history: [],
    }),
  },
}));

describe('ObicoSettings status panel', () => {
  it('shows the pause level', async () => {
    render(<ObicoSettings />);

    expect(await screen.findByText('0.38 / 0.78')).toBeInTheDocument();
    expect(screen.getByText('1.40')).toBeInTheDocument();
  });
});
