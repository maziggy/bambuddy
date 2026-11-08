/**
 * The signed-in user's personal access code for virtual printers (#1430).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SlicerAccessCodeModal } from '../../components/SlicerAccessCodeModal';

vi.mock('../../api/client', () => ({
  multiVirtualPrinterApi: {
    getMyAccessCode: vi.fn(),
    renewMyAccessCode: vi.fn(),
  },
  api: { getSettings: vi.fn().mockResolvedValue({}) },
}));

import { multiVirtualPrinterApi } from '../../api/client';

describe('SlicerAccessCodeModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(multiVirtualPrinterApi.getMyAccessCode).mockResolvedValue({ code: '12345678' });
    vi.mocked(multiVirtualPrinterApi.renewMyAccessCode).mockResolvedValue({ code: '87654321' });
  });

  it('shows the code hidden until asked', async () => {
    const user = userEvent.setup();
    render(<SlicerAccessCodeModal onClose={() => {}} />);

    const code = await screen.findByTestId('slicer-access-code');
    await waitFor(() => expect(code.textContent).toBe('•'.repeat(8)));

    await user.click(screen.getByRole('button', { name: 'Show code' }));

    expect(screen.getByTestId('slicer-access-code').textContent).toBe('12345678');
  });

  it('a new code replaces the old one after confirming', async () => {
    const user = userEvent.setup();
    render(<SlicerAccessCodeModal onClose={() => {}} />);
    await screen.findByTestId('slicer-access-code');

    await user.click(await screen.findByRole('button', { name: /New code/ }));
    const title = await screen.findByText('Replace your access code?');
    const dialog = title.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'New code' }));

    await waitFor(() => expect(screen.getByTestId('slicer-access-code').textContent).toBe('87654321'));
    expect(multiVirtualPrinterApi.renewMyAccessCode).toHaveBeenCalledTimes(1);
  });

  it('cancelling keeps the code', async () => {
    const user = userEvent.setup();
    render(<SlicerAccessCodeModal onClose={() => {}} />);
    await screen.findByTestId('slicer-access-code');

    await user.click(await screen.findByRole('button', { name: /New code/ }));
    await screen.findByText('Replace your access code?');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(multiVirtualPrinterApi.renewMyAccessCode).not.toHaveBeenCalled();
  });
});
