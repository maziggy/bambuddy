import { describe, it, expect, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { VariantCandidates, type VariantCandidate } from '../../components/PrintModal/VariantCandidates';

describe('VariantCandidates', () => {
  it('chooses the plate it shows when none was picked', async () => {
    server.use(
      http.get('/api/v1/library/files/:id/plates', () =>
        HttpResponse.json({
          is_multi_plate: true,
          plates: [
            { index: 1, name: 'Plate 1' },
            { index: 2, name: 'Plate 2' },
          ],
        }),
      ),
    );
    const onPlateChange = vi.fn();
    const candidates = [{ id: 7, filename: 'part.3mf', sliced_for_model: 'X1C' }] as VariantCandidate[];

    render(
      <VariantCandidates candidates={candidates} onReorder={vi.fn()} plateByFile={{}} onPlateChange={onPlateChange} />,
    );

    await waitFor(() => expect(onPlateChange).toHaveBeenCalledWith(7, 1));
  });
});
