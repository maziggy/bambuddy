/**
 * Tests for AlertModal — the acknowledge-only error modal used to surface
 * slice failures (and other must-read errors) that a toast would auto-dismiss
 * before they can be read.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../i18n';
import { AlertModal } from '../../components/AlertModal';

function renderModal(props?: Partial<Parameters<typeof AlertModal>[0]>) {
  const onClose = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <AlertModal
        title="Slicing failed"
        subtitle="Mecha Mewtwo.3mf"
        message="Some objects are located over the boundary of the heated bed."
        onClose={onClose}
        {...props}
      />
    </I18nextProvider>,
  );
  return { onClose };
}

describe('AlertModal', () => {
  it('renders the title, subtitle and message', () => {
    renderModal();
    expect(screen.getByText('Slicing failed')).toBeInTheDocument();
    expect(screen.getByText('Mecha Mewtwo.3mf')).toBeInTheDocument();
    expect(
      screen.getByText('Some objects are located over the boundary of the heated bed.'),
    ).toBeInTheDocument();
  });

  it('calls onClose when the Close button is clicked', () => {
    const { onClose } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when Escape is pressed', () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('omits the subtitle line when no subtitle is given', () => {
    renderModal({ subtitle: undefined });
    expect(screen.queryByText('Mecha Mewtwo.3mf')).not.toBeInTheDocument();
    expect(screen.getByText('Slicing failed')).toBeInTheDocument();
  });
});
