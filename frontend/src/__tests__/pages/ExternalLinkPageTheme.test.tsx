/**
 * The external-link page tells the framed app which theme Bambuddy shows, so
 * an app made for the sidebar (Bambuddy Orders) can match it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { render } from '../utils';
import { api } from '../../api/client';
import { ExternalLinkPage } from '../../pages/ExternalLinkPage';

const LINK = {
  id: 5,
  name: 'Orders',
  url: 'http://orders.local:8090/',
  icon: 'shopping-cart',
  open_in_new_tab: false,
  custom_icon: null,
  sort_order: 0,
  created_at: '2026-09-27T00:00:00Z',
  updated_at: '2026-09-27T00:00:00Z',
};

function renderPage() {
  window.history.pushState({}, '', '/external/5');
  return render(
    <Routes>
      <Route path="/external/:id" element={<ExternalLinkPage />} />
    </Routes>,
  );
}

beforeEach(() => {
  vi.spyOn(api, 'getExternalLink').mockResolvedValue(LINK);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// The values come from the theme context (local choice plus the server
// settings sync); the contract is the message's shape.
const THEME = {
  type: 'bambuddy:theme',
  mode: expect.stringMatching(/^(light|dark)$/),
  style: expect.any(String),
  background: expect.any(String),
  accent: expect.any(String),
};

describe('ExternalLinkPage theme', () => {
  it('sends the theme to the link origin when the page loads', async () => {
    renderPage();
    const frame = (await screen.findByTitle('Orders')) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledWith(THEME, 'http://orders.local:8090');
  });

  it('answers when the framed page asks for the theme', async () => {
    renderPage();
    const frame = (await screen.findByTitle('Orders')) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: 'bambuddy:theme-request' }, source: frame.contentWindow }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledWith(THEME, 'http://orders.local:8090'));
  });

  it('ignores theme requests from other windows', async () => {
    renderPage();
    const frame = (await screen.findByTitle('Orders')) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'bambuddy:theme-request' }, source: window }));
    expect(post).not.toHaveBeenCalled();
  });
});
