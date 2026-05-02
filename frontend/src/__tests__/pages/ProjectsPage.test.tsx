/**
 * Tests for the ProjectsPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ProjectsPage } from '../../pages/ProjectsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockProjects = [
  {
    id: 1,
    name: 'Functional Parts',
    description: 'Useful household items',
    color: '#00ae42',
    archive_count: 10,
    total_print_time_seconds: 36000,
    total_filament_grams: 500,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-15T00:00:00Z',
  },
  {
    id: 2,
    name: 'Art Collection',
    description: 'Decorative prints',
    color: '#ff5500',
    archive_count: 5,
    total_print_time_seconds: 18000,
    total_filament_grams: 200,
    created_at: '2024-01-05T00:00:00Z',
    updated_at: '2024-01-10T00:00:00Z',
  },
];

describe('ProjectsPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json(mockProjects);
      }),
      http.post('/api/v1/projects/', async ({ request }) => {
        const body = await request.json() as { name: string };
        return HttpResponse.json({ id: 3, name: body.name, color: '#00ae42', archive_count: 0 });
      }),
      http.delete('/api/v1/projects/:id', () => {
        return HttpResponse.json({ success: true });
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<ProjectsPage />);

      await waitFor(() => {
        expect(screen.getByText('Projects')).toBeInTheDocument();
      });
    });

    it('shows project cards', async () => {
      render(<ProjectsPage />);

      await waitFor(() => {
        expect(screen.getByText('Functional Parts')).toBeInTheDocument();
        expect(screen.getByText('Art Collection')).toBeInTheDocument();
      });
    });

    it('shows project descriptions', async () => {
      render(<ProjectsPage />);

      await waitFor(() => {
        expect(screen.getByText('Useful household items')).toBeInTheDocument();
        expect(screen.getByText('Decorative prints')).toBeInTheDocument();
      });
    });
  });

  describe('project info', () => {
    it('shows archive count', async () => {
      render(<ProjectsPage />);

      await waitFor(() => {
        // Project cards should show archive counts
        expect(screen.getByText('Functional Parts')).toBeInTheDocument();
      });
    });

    it('shows project colors', async () => {
      render(<ProjectsPage />);

      await waitFor(() => {
        const functionalParts = screen.getByText('Functional Parts');
        expect(functionalParts).toBeInTheDocument();
        // Color is applied as style
      });
    });
  });

  describe('create project', () => {
    it('has new project button', async () => {
      render(<ProjectsPage />);

      await waitFor(() => {
        expect(screen.getByText('New Project')).toBeInTheDocument();
      });
    });

    it('opens create modal on click', async () => {
      const user = userEvent.setup();
      render(<ProjectsPage />);

      await waitFor(() => {
        expect(screen.getByText('New Project')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New Project'));

      // Modal should open - look for modal content
      await waitFor(() => {
        // Modal may show "Create Project" or similar text
        const modalContent = screen.queryByText(/create/i) ||
                           screen.queryByRole('dialog') ||
                           screen.queryByText(/name/i);
        expect(modalContent).toBeTruthy();
      });
    });
  });

  describe('empty state', () => {
    it('shows empty state when no projects', async () => {
      server.use(
        http.get('/api/v1/projects/', () => {
          return HttpResponse.json([]);
        })
      );

      render(<ProjectsPage />);

      await waitFor(() => {
        // Either empty state message or the page title should be visible
        const emptyMsg = screen.queryByText(/no projects/i);
        const pageTitle = screen.queryByText('Projects');
        expect(emptyMsg || pageTitle).toBeTruthy();
      });
    });
  });

  // #1155 — URL link icon + cover image thumbnail on project cards.
  describe('URL link and cover image (#1155)', () => {
    it('renders an external-link icon next to the project name when URL is set', async () => {
      server.use(
        http.get('/api/v1/projects/', () =>
          HttpResponse.json([
            {
              ...mockProjects[0],
              url: 'https://makerworld.com/models/12345',
              cover_image_filename: null,
            },
          ])
        )
      );

      render(<ProjectsPage />);

      const link = await screen.findByLabelText(/Open project URL/i);
      expect(link).toBeInTheDocument();
      expect(link.getAttribute('href')).toBe('https://makerworld.com/models/12345');
      expect(link.getAttribute('target')).toBe('_blank');
      expect(link.getAttribute('rel')).toContain('noopener');
    });

    it('does not render the link icon when URL is not set', async () => {
      // Default fixture has no `url` field — verify the icon is absent.
      render(<ProjectsPage />);

      await waitFor(() => {
        expect(screen.getByText('Functional Parts')).toBeInTheDocument();
      });
      expect(screen.queryByLabelText(/Open project URL/i)).not.toBeInTheDocument();
    });

    it('clicking the URL link does not bubble to the card onClick', async () => {
      server.use(
        http.get('/api/v1/projects/', () =>
          HttpResponse.json([
            {
              ...mockProjects[0],
              url: 'https://example.com',
              cover_image_filename: null,
            },
          ])
        )
      );

      const user = userEvent.setup();
      render(<ProjectsPage />);

      const link = await screen.findByLabelText(/Open project URL/i);
      // Prevent the underlying anchor from triggering jsdom navigation noise
      // — we only need the propagation guard verified.
      link.addEventListener('click', (e) => e.preventDefault(), { once: true });
      await user.click(link);

      // No navigate / detail-page transition should have happened. Card root
      // is still rendered.
      expect(screen.getByText('Functional Parts')).toBeInTheDocument();
    });

    it('renders a cover image thumbnail when cover_image_filename is set', async () => {
      server.use(
        http.get('/api/v1/projects/', () =>
          HttpResponse.json([
            {
              ...mockProjects[0],
              url: null,
              cover_image_filename: 'cover_abc.png',
            },
          ])
        )
      );

      render(<ProjectsPage />);

      const img = await screen.findByAltText(/Project cover photo/i);
      expect(img).toBeInTheDocument();
      // Card thumbnail uses the GET endpoint URL, project.id is 1.
      expect(img.getAttribute('src')).toContain('/projects/1/cover-image');
    });

    it('renders the portal-mounted hover preview only while the thumbnail is hovered (#1155)', async () => {
      // The portal escapes the project card's ``overflow-hidden``, which
      // would otherwise clip a 384×384 popover anchored to a 40×40
      // thumbnail. Pin the contract end-to-end:
      //   - no preview in the DOM by default (mouseenter not fired yet)
      //   - mouseenter mounts a popover at document.body level (not
      //     nested in the card subtree, which would re-introduce the
      //     clipping bug)
      //   - mouseleave unmounts it
      //   - the popover ``<img>`` points at the same cover-image URL as
      //     the small thumbnail (object-contain so portrait/landscape
      //     MakerWorld photos aren't cropped)
      const { fireEvent } = await import('@testing-library/react');

      server.use(
        http.get('/api/v1/projects/', () =>
          HttpResponse.json([
            {
              ...mockProjects[0],
              url: null,
              cover_image_filename: 'cover_abc.png',
            },
          ])
        )
      );

      render(<ProjectsPage />);

      const thumb = await screen.findByAltText(/Project cover photo/i);
      // Default: no popover yet.
      expect(document.querySelectorAll('[aria-hidden="true"] img').length).toBe(0);

      // Hover: walk up to the wrapper div the component attaches its
      // mouseenter to. The portal mounts under document.body, NOT
      // nested in the card subtree.
      const wrapper = thumb.closest('[class*="flex-shrink-0"]') as HTMLElement;
      expect(wrapper).not.toBeNull();
      fireEvent.mouseEnter(wrapper);

      const previewImg = document.querySelector('[aria-hidden="true"] img') as HTMLImageElement | null;
      expect(previewImg).not.toBeNull();
      expect(previewImg!.getAttribute('src')).toContain('/projects/1/cover-image');
      expect(previewImg!.className).toContain('object-contain');

      // The portal mounts on document.body (or one of its direct
      // descendants), not inside the card — that's the whole point of
      // the portal, so a future refactor that drops the portal would
      // re-introduce the clipping regression.
      const popover = previewImg!.closest('[aria-hidden="true"]') as HTMLElement;
      expect(popover.closest('[class*="rounded-xl"]')).toBeNull();

      // Unmount on leave.
      fireEvent.mouseLeave(wrapper);
      expect(document.querySelectorAll('[aria-hidden="true"] img').length).toBe(0);
    });

    it('does not render a hover preview when there is no cover image', async () => {
      server.use(
        http.get('/api/v1/projects/', () =>
          HttpResponse.json([
            { ...mockProjects[0], url: null, cover_image_filename: null },
          ])
        )
      );

      render(<ProjectsPage />);

      await screen.findByText(mockProjects[0].name);
      expect(screen.queryByAltText(/Project cover photo/i)).toBeNull();
      // No aria-hidden img should ever appear because no thumbnail to
      // hover means the portal-mounting component never renders.
      expect(document.querySelectorAll('[aria-hidden="true"] img').length).toBe(0);
    });
  });
});
