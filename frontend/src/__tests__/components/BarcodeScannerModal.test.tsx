/**
 * Tests for BarcodeScannerModal's manual-entry lookup path.
 *
 * jsdom has no navigator.mediaDevices, so hasCameraSupport() is always false
 * in this environment — the Scan tab is hidden and Photo is the default tab.
 * These tests focus on the always-available Manual Entry path, which is also
 * the only path that doesn't depend on camera/OCR libraries.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { BarcodeScannerModal } from '../../components/BarcodeScannerModal';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    lookupFilamentBarcode: vi.fn(),
    parseFilamentLabel: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

import { api } from '../../api/client';

describe('BarcodeScannerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('hides the Scan tab and defaults to the Photo tab without camera support', async () => {
    render(<BarcodeScannerModal onClose={vi.fn()} onResolved={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Choose Photo')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /^scan$/i })).not.toBeInTheDocument();
  });

  it('disables the Look Up button until a barcode is typed', async () => {
    render(<BarcodeScannerModal onClose={vi.fn()} onResolved={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /manual entry/i }));

    const lookUpButton = await screen.findByRole('button', { name: /look up/i });
    expect(lookUpButton).toBeDisabled();

    const input = screen.getByPlaceholderText(/6938936716785/);
    fireEvent.change(input, { target: { value: '6938936716785' } });
    expect(lookUpButton).not.toBeDisabled();
  });

  it('resolves a matched barcode via manual entry and calls onResolved', async () => {
    vi.mocked(api.lookupFilamentBarcode).mockResolvedValueOnce({
      enabled: true,
      matched: true,
      source: 'inventory',
      barcode: '6938936716785',
      material: 'PLA',
      brand: 'Sunlu',
      subtype: 'Plus',
      color_name: 'Black',
      rgba: '000000FF',
      label_weight: 1000,
      nozzle_temp_min: 190,
      nozzle_temp_max: 230,
    });

    const onResolved = vi.fn();
    render(<BarcodeScannerModal onClose={vi.fn()} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole('button', { name: /manual entry/i }));
    const input = await screen.findByPlaceholderText(/6938936716785/);
    fireEvent.change(input, { target: { value: '6938936716785' } });
    fireEvent.click(screen.getByRole('button', { name: /look up/i }));

    await waitFor(() => {
      expect(api.lookupFilamentBarcode).toHaveBeenCalledWith('6938936716785');
    });

    await waitFor(() => {
      expect(onResolved).toHaveBeenCalledWith(
        expect.objectContaining({
          barcode: '6938936716785',
          matched: true,
          source: 'inventory',
          material: 'PLA',
          brand: 'Sunlu',
        }),
      );
    });
  });

  it('calls onResolved with matched:false when the barcode has no match', async () => {
    vi.mocked(api.lookupFilamentBarcode).mockResolvedValueOnce({
      enabled: true,
      matched: false,
      source: null,
      barcode: '000000000000',
      material: null,
      brand: null,
      subtype: null,
      color_name: null,
      rgba: null,
      label_weight: null,
      nozzle_temp_min: null,
      nozzle_temp_max: null,
    });

    const onResolved = vi.fn();
    render(<BarcodeScannerModal onClose={vi.fn()} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole('button', { name: /manual entry/i }));
    const input = await screen.findByPlaceholderText(/6938936716785/);
    fireEvent.change(input, { target: { value: '000000000000' } });
    fireEvent.click(screen.getByRole('button', { name: /look up/i }));

    await waitFor(() => {
      expect(onResolved).toHaveBeenCalledWith(
        expect.objectContaining({ barcode: '000000000000', matched: false, source: null }),
      );
    });
  });

  it('does not call onResolved when the lookup request fails', async () => {
    vi.mocked(api.lookupFilamentBarcode).mockRejectedValueOnce(new Error('network error'));

    const onResolved = vi.fn();
    render(<BarcodeScannerModal onClose={vi.fn()} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole('button', { name: /manual entry/i }));
    const input = await screen.findByPlaceholderText(/6938936716785/);
    fireEvent.change(input, { target: { value: '123456789012' } });
    fireEvent.click(screen.getByRole('button', { name: /look up/i }));

    await waitFor(() => {
      expect(api.lookupFilamentBarcode).toHaveBeenCalledTimes(1);
    });
    expect(onResolved).not.toHaveBeenCalled();
  });

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn();
    render(<BarcodeScannerModal onClose={onClose} onResolved={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Choose Photo')).toBeInTheDocument();
    });

    const closeButtons = document.querySelectorAll('button');
    const closeButton = Array.from(closeButtons).find((btn) => btn.querySelector('svg.lucide-x'));
    expect(closeButton).toBeTruthy();
    fireEvent.click(closeButton!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
