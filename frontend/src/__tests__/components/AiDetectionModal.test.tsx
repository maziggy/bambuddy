import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AiDetectionModal } from '../../components/AiDetectionModal';
import { setAuthToken } from '../../api/client';

const limitDetection = {
  class: 'error',
  frame_count: 0,
  print_quality: null,
  error: 'Remote quota error',
  error_code: 'OE_FREE_USAGE_LIMIT_REACHED',
};

afterEach(() => setAuthToken(null));

describe('AiDetectionModal usage limit', () => {
  it('uses a structured printer error code and clears the notice when that printer recovers', async () => {
    const { rerender } = render(<AiDetectionModal printerName="X1 Carbon" detection={limitDetection} lastError={null} onClose={vi.fn()} />);

    expect(await screen.findByText('Usage limit reached.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Set up billing to continue' })).toHaveAttribute('href', 'https://octoeverywhere.com/gadgetapi');
    expect(screen.queryByText('Remote quota error')).not.toBeInTheDocument();

    rerender(<AiDetectionModal printerName="X1 Carbon" detection={{ class: 'safe', frame_count: 1, print_quality: 10, error: null, error_code: null }} lastError={null} onClose={vi.fn()} />);
    expect(screen.queryByText('Usage limit reached.')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
    expect(screen.getByText('10/10')).toBeInTheDocument();
  });

  it('uses the global reason and code together when this printer has no error', async () => {
    render(<AiDetectionModal printerName="X1 Carbon" lastError="Remote global error" lastErrorCode="OE_FREE_USAGE_LIMIT_REACHED" onClose={vi.fn()} />);

    expect(await screen.findByText('Usage limit reached.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Set up billing to continue' })).toBeInTheDocument();
    expect(screen.queryByText('Remote global error')).not.toBeInTheDocument();
  });

  it('does not attach another printer’s usage-limit code to this printer’s camera error', async () => {
    render(<AiDetectionModal
      printerName="X1 Carbon"
      detection={{ ...limitDetection, error: 'Camera unavailable', error_code: null }}
      lastError="Another printer reached its usage limit"
      lastErrorCode="OE_FREE_USAGE_LIMIT_REACHED"
      onClose={vi.fn()}
    />);

    expect(await screen.findByText('Camera unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Usage limit reached.')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
  });

  it('does not infer a usage-limit error from unstructured text', async () => {
    render(<AiDetectionModal printerName="X1 Carbon" lastError="OE_FREE_USAGE_LIMIT_REACHED" onClose={vi.fn()} />);

    expect(await screen.findByText('OE_FREE_USAGE_LIMIT_REACHED')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
  });

  it.each([
    [[], false],
    [['settings:read'], true],
  ])('respects settings:read permission for quota details and the billing setup link (%j)', async (permissions, canRead) => {
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () => HttpResponse.json({ id: 2, username: 'viewer', is_admin: false, permissions })),
    );
    render(<AiDetectionModal printerName="X1 Carbon" detection={limitDetection} lastError={null} onClose={vi.fn()} />);

    if (canRead) {
      expect(await screen.findByText('Usage limit reached.')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /set up billing/i })).toBeInTheDocument();
    } else {
      await waitFor(() => expect(screen.queryByText('Usage limit reached.')).not.toBeInTheDocument());
      expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
    }
    expect(screen.getByText('Not checking')).toBeInTheDocument();
  });
});
