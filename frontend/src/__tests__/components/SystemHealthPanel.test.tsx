/**
 * Tests for SystemHealthPanel — the shared log-health result renderer used by
 * the System page and the bug reporter.
 *
 * Covers the three states (clean / log unavailable / findings) and that a
 * finding renders its localized name, cause, fix, category badge, and a wiki
 * deep-link built from the signature's anchor.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../i18n';
import { SystemHealthPanel } from '../../components/SystemHealthPanel';
import type { SystemHealthResult } from '../../api/client';

function renderPanel(result: SystemHealthResult) {
  render(
    <I18nextProvider i18n={i18n}>
      <SystemHealthPanel result={result} />
    </I18nextProvider>,
  );
}

const BASE: SystemHealthResult = {
  findings: [],
  scanned_entries: 1200,
  log_available: true,
  summary: { total: 0, layer8: 0, environment: 0, bug: 0 },
};

describe('SystemHealthPanel', () => {
  it('shows a healthy message when there are no findings', () => {
    renderPanel(BASE);
    expect(screen.getByText(/No known issues found/i)).toBeInTheDocument();
  });

  it('shows a notice when file logging is unavailable', () => {
    renderPanel({ ...BASE, log_available: false });
    expect(screen.getByText(/File logging is disabled/i)).toBeInTheDocument();
  });

  it('renders a finding with localized name, cause, fix, badge, and wiki link', () => {
    renderPanel({
      ...BASE,
      log_available: true,
      findings: [
        {
          signature_id: 'ftp-auth-rejected',
          severity: 'error',
          category: 'layer8',
          wiki_anchor: 'wrong-access-code',
          count: 4,
          first_seen: '2026-05-22 09:00:00,000',
          last_seen: '2026-05-22 10:00:00,000',
          sample: 'FTP connection permission error to [IP]',
        },
      ],
      summary: { total: 1, layer8: 1, environment: 0, bug: 0 },
    });

    expect(screen.getByText('Printer rejected the access code')).toBeInTheDocument();
    expect(screen.getByText(/refused the file-transfer login/i)).toBeInTheDocument();
    expect(screen.getByText(/Re-copy the access code/i)).toBeInTheDocument();
    expect(screen.getByText('You can fix this')).toBeInTheDocument();
    expect(screen.getByText('FTP connection permission error to [IP]')).toBeInTheDocument();

    const link = screen.getByRole('link', { name: /How to fix/i });
    expect(link).toHaveAttribute(
      'href',
      'https://wiki.bambuddy.cool/reference/troubleshooting/#wrong-access-code',
    );
  });
});
