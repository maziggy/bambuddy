/**
 * Which projects a picker offers when filing something away (#2888).
 *
 * The reporter had five active projects sitting behind thirty-odd finished
 * ones, and every dropdown that assigns an archive listed all of them. This is
 * the rule those pickers now share: archived out, completed in, and whatever
 * the thing being edited already belongs to stays regardless.
 */

import { describe, it, expect } from 'vitest';
import { assignableProjects } from '../../utils/projectTree';
import type { ProjectListItem } from '../../api/client';

const project = (over: Partial<ProjectListItem>): ProjectListItem => ({
  id: 1,
  name: 'Airframe',
  description: null,
  color: '#00ae42',
  status: 'active',
  target_count: null,
  target_parts_count: null,
  target_sets: null,
  budget: null,
  tags: null,
  due_date: null,
  priority: 'normal',
  created_at: '2026-01-01T00:00:00Z',
  archive_count: 0,
  total_items: 0,
  completed_count: 0,
  failed_count: 0,
  queue_count: 0,
  progress_percent: null,
  parent_id: null,
  child_count: 0,
  archives: [],
  url: null,
  cover_image_filename: null,
  ...over,
});

const ids = (rows: ProjectListItem[]) => rows.map((p) => p.id);

describe('assignableProjects', () => {
  it('drops archived projects', () => {
    const projects = [
      project({ id: 1, status: 'active' }),
      project({ id: 2, status: 'archived' }),
    ];

    expect(ids(assignableProjects(projects))).toEqual([1]);
  });

  it('keeps completed projects', () => {
    // Filing a reprint against a finished project is ordinary work --
    // "completed" says the job is done, not that it should be hidden.
    const projects = [
      project({ id: 1, status: 'active' }),
      project({ id: 2, status: 'completed' }),
      project({ id: 3, status: 'archived' }),
    ];

    expect(ids(assignableProjects(projects))).toEqual([1, 2]);
  });

  it('keeps the archived project the caller names', () => {
    // A controlled <select> holding a value no option matches is reset by the
    // browser to its first option -- "No project" -- so an archive filed in an
    // archived project would claim to be filed nowhere.
    const projects = [
      project({ id: 1, status: 'active' }),
      project({ id: 2, status: 'archived' }),
    ];

    expect(ids(assignableProjects(projects, 2))).toEqual([1, 2]);
  });

  it('keeps only the named archived project, not archived ones generally', () => {
    const projects = [
      project({ id: 2, status: 'archived' }),
      project({ id: 3, status: 'archived' }),
    ];

    expect(ids(assignableProjects(projects, 3))).toEqual([3]);
  });

  it('treats no current project as naming nothing', () => {
    // The pickers pass `archive.project_id` straight through, and an
    // unassigned archive carries null there. Reading null as an id would be
    // harmless today only because no project has that id -- but a picker with
    // no argument at all must behave the same way.
    const projects = [project({ id: 1, status: 'archived' })];

    expect(assignableProjects(projects, null)).toEqual([]);
    expect(assignableProjects(projects, undefined)).toEqual([]);
    expect(assignableProjects(projects)).toEqual([]);
  });

  it('leaves the order it was given', () => {
    // Every caller sorts by name either side of this, so reordering here
    // would silently fight them.
    const projects = [
      project({ id: 3, name: 'Wing' }),
      project({ id: 1, name: 'Airframe' }),
      project({ id: 2, name: 'Spar' }),
    ];

    expect(ids(assignableProjects(projects))).toEqual([3, 1, 2]);
  });

  it('does not modify the array it is given', () => {
    const projects = [
      project({ id: 1, status: 'active' }),
      project({ id: 2, status: 'archived' }),
    ];

    assignableProjects(projects);

    expect(ids(projects)).toEqual([1, 2]);
  });
});
