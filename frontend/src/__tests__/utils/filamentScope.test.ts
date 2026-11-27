import { describe, it, expect } from 'vitest';
import {
  blockedFilamentNames,
  filamentAllowed,
  filamentName,
  judgedFilaments,
  markBlocked,
  withFilamentRule,
} from '../../utils/filamentScope';
import type { FilamentPolicy } from '../../api/client';
import shared from './filamentScopeCases.json';

const blockMatte: FilamentPolicy[] = [{ group: 'friends', mode: 'block', rules: [{ match: 'filament_id', value: 'GFG03' }] }];
const allowPla: FilamentPolicy[] = [{ group: 'friends', mode: 'allow', rules: [{ match: 'material', value: 'PLA' }] }];

describe('filamentAllowed', () => {
  it('treats null or undefined policies as unrestricted', () => {
    expect(filamentAllowed(null, 'PETG', 'GFG03')).toBe(true);
    expect(filamentAllowed(undefined, 'PETG', 'GFG03')).toBe(true);
  });
  it('applies a block list by filament id, case-insensitively', () => {
    expect(filamentAllowed(blockMatte, 'PETG', 'gfg03')).toBe(false);
    expect(filamentAllowed(blockMatte, 'PETG', 'GFG99')).toBe(true);
  });
  it('matches material types exactly', () => {
    expect(filamentAllowed(allowPla, 'pla', '')).toBe(true);
    expect(filamentAllowed(allowPla, 'PLA-CF', '')).toBe(false);
  });
  it('allows when any policy allows', () => {
    const both: FilamentPolicy[] = [...blockMatte, { group: 'b', mode: 'allow', rules: [{ match: 'filament_id', value: 'GFG03' }] }];
    expect(filamentAllowed(both, 'PETG', 'GFG03')).toBe(true);
  });
  it('allows a filament with neither type nor id', () => {
    expect(filamentAllowed(allowPla, '', '')).toBe(true);
  });
  it('allows nothing identifiable with an empty policy list', () => {
    expect(filamentAllowed([], 'PLA', '')).toBe(false);
  });
  it('ignores a rule with an empty value, as the server does', () => {
    const blankBlock: FilamentPolicy[] = [{ group: 'b', mode: 'block', rules: [{ match: 'material', value: '  ' }] }];
    const blankAllow: FilamentPolicy[] = [{ group: 'a', mode: 'allow', rules: [{ match: 'material', value: '' }] }];
    expect(filamentAllowed(blankBlock, 'PLA', '')).toBe(true);
    expect(filamentAllowed(blankAllow, 'PLA', '')).toBe(false);
  });
  it('judges a filament with an id but no type by its id', () => {
    expect(filamentAllowed(blockMatte, '', 'GFG03')).toBe(false);
    expect(filamentAllowed(blockMatte, '', 'GFG99')).toBe(true);
  });
});

describe('markBlocked', () => {
  it('flags blocked trays and leaves the rest untouched', () => {
    const loaded = [
      { globalTrayId: 0, type: 'PETG', trayInfoIdx: 'GFG03' },
      { globalTrayId: 1, type: 'PETG', trayInfoIdx: 'GFG99' },
    ];
    const marked = markBlocked(loaded, blockMatte);
    expect(marked.map((f) => !!f.blocked)).toEqual([true, false]);
    expect(marked[1]).toBe(loaded[1]);
    expect(markBlocked(loaded, null)).toBe(loaded);
  });
});

describe('filamentName', () => {
  it('reads TYPE (ID), or TYPE alone without an id', () => {
    expect(filamentName('PETG', 'GFG03')).toBe('PETG (GFG03)');
    expect(filamentName('PETG', '')).toBe('PETG');
    expect(filamentName('PETG', undefined)).toBe('PETG');
  });
  it('mirrors the server: trimmed, and the id alone without a type', () => {
    expect(filamentName(' PETG ', ' GFG03 ')).toBe('PETG (GFG03)');
    expect(filamentName('', 'GFG03')).toBe('GFG03');
    expect(filamentName('  ', '  ')).toBe('');
  });
});

describe('blockedFilamentNames', () => {
  it('names each blocked requirement once, in order', () => {
    const reqs = [
      { type: 'PETG', tray_info_idx: 'GFG03' },
      { type: 'PETG', tray_info_idx: 'GFG99' },
      { type: 'PETG', tray_info_idx: 'GFG03' },
    ];
    expect(blockedFilamentNames(reqs, blockMatte)).toEqual(['PETG (GFG03)']);
  });
  it('names a requirement with no id by its type', () => {
    expect(blockedFilamentNames([{ type: 'PETG' }, { type: 'PLA' }], allowPla)).toEqual(['PETG']);
  });
  it('is empty when unrestricted or there are no requirements', () => {
    expect(blockedFilamentNames([{ type: 'PETG', tray_info_idx: 'GFG03' }], null)).toEqual([]);
    expect(blockedFilamentNames(undefined, blockMatte)).toEqual([]);
  });
});

describe('withFilamentRule', () => {
  it('adds a trimmed rule, upper-casing a material, once', () => {
    const rules = withFilamentRule([], 'material', ' petg ');
    expect(rules).toEqual([{ match: 'material', value: 'PETG' }]);
    expect(withFilamentRule(rules, 'material', 'Petg')).toBe(rules);
    expect(withFilamentRule(rules, 'filament_id', ' GFG03 ')).toEqual([...rules, { match: 'filament_id', value: 'GFG03' }]);
  });
  it('returns the same list for a blank value', () => {
    const rules = [{ match: 'material' as const, value: 'PLA' }];
    expect(withFilamentRule(rules, 'material', '   ')).toBe(rules);
  });
});

// The same cases backend/tests/unit/test_filament_scope.py feeds the server's
// rules, so the dialog never greys out what the server allows or the reverse.
describe('filamentAllowed agrees with the server', () => {
  it.each(shared.cases)('$name', (c) => {
    const policies: FilamentPolicy[] = [
      { group: 'friends', mode: c.mode as FilamentPolicy['mode'], rules: c.rules as FilamentPolicy['rules'] },
    ];
    expect(filamentAllowed(policies, c.type, c.filament_id)).toBe(c.allowed);
  });
});

// Mirrors TestJudgedRequirements in backend/tests/unit/test_filament_scope.py.
describe('judgedFilaments', () => {
  const reqs = [
    { slot_id: 1, type: 'PETG', tray_info_idx: 'GFG03' },
    { slot_id: 2, type: 'PLA', tray_info_idx: 'GFA00' },
  ];
  it('judges the file as sliced without overrides', () => {
    expect(judgedFilaments(reqs, undefined)).toEqual(reqs);
  });
  it('leaves a swap to the tray matcher', () => {
    expect(judgedFilaments(reqs, [{ slot_id: 1, type: 'PETG', force_color_match: false }])).toEqual([reqs[1]]);
  });
  it('judges a forced colour naming an id as it names it', () => {
    expect(
      judgedFilaments(reqs, [{ slot_id: 2, type: 'PETG', tray_info_idx: 'GFG03', force_color_match: true }]),
    ).toEqual([reqs[0], { slot_id: 2, type: 'PETG', tray_info_idx: 'GFG03' }]);
  });
  it('leaves a forced colour naming no id to the tray matcher', () => {
    expect(judgedFilaments(reqs, [{ slot_id: 1, type: 'PETG', force_color_match: true }])).toEqual([reqs[1]]);
  });
});
