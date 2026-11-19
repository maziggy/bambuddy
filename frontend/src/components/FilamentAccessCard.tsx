import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Plus, Spool, X } from 'lucide-react';
import type { FilamentRule, FilamentRuleMode } from '../api/client';
import { Button } from './Button';
import { Card } from './Card';
import { withFilamentRule } from '../utils/filamentScope';

// Suggestions only: any type the printer reports can be typed in.
const MATERIAL_SUGGESTIONS = ['PLA', 'PETG', 'PCTG', 'ABS', 'ASA', 'TPU', 'PC', 'PA', 'PA-CF', 'PAHT-CF', 'PET-CF', 'PVA', 'HIPS', 'PP'];

export interface FilamentAccessValue {
  enabled: boolean;
  mode: FilamentRuleMode;
  rules: FilamentRule[];
}

interface FilamentAccessCardProps extends FilamentAccessValue {
  /** Administrators can't be limited: show a note instead of controls */
  disabled?: boolean;
  onChange: (value: FilamentAccessValue) => void;
  /**
   * The rules with whatever is typed in the input but not yet added. The page
   * saves these, so a value typed and never added isn't lost on Save: in
   * "allow" mode that would leave an empty list, which blocks every filament.
   */
  onPendingRulesChange?: (rules: FilamentRule[]) => void;
}

/** Which filaments a group's members may print with (#3522). */
export function FilamentAccessCard({
  enabled,
  mode,
  rules,
  disabled = false,
  onChange,
  onPendingRulesChange,
}: FilamentAccessCardProps) {
  const { t } = useTranslation();
  const [match, setMatch] = useState<FilamentRule['match']>('material');
  const [value, setValue] = useState('');

  // Typed text goes when the limit is switched off, which hides the input:
  // Save must not store a rule nobody can see (#3522).
  const [wasEnabled, setWasEnabled] = useState(enabled);
  if (enabled !== wasEnabled) {
    setWasEnabled(enabled);
    if (!enabled) setValue('');
  }

  const emit = (next: Partial<FilamentAccessValue>) => onChange({ enabled, mode, rules, ...next });

  useEffect(() => {
    onPendingRulesChange?.(enabled ? withFilamentRule(rules, match, value) : rules);
  }, [enabled, rules, match, value, onPendingRulesChange]);

  const addRule = () => {
    const next = withFilamentRule(rules, match, value);
    if (next !== rules) emit({ rules: next });
    setValue('');
  };

  return (
    <Card>
      <div className="p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Spool className="w-4 h-4 text-bambu-gray shrink-0" />
          <span className="text-white font-medium text-sm">{t('groups.editor.filamentAccess')}</span>
        </div>

        {disabled ? (
          <p className="text-sm text-bambu-gray">{t('groups.editor.adminsUseAllFilaments')}</p>
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm text-white cursor-pointer">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => emit({ enabled: e.target.checked })}
                className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark-secondary"
              />
              {t('groups.editor.limitFilaments')}
            </label>

            {!enabled ? (
              <p className="text-sm text-bambu-gray">{t('groups.editor.filamentAccessOpen')}</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-4 text-sm text-white">
                  {(['block', 'allow'] as const).map((m) => (
                    <label key={m} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="filament-rule-mode"
                        checked={mode === m}
                        onChange={() => emit({ mode: m })}
                        className="text-bambu-green focus:ring-bambu-green bg-bambu-dark-secondary"
                      />
                      {m === 'block' ? t('groups.editor.filamentModeBlock') : t('groups.editor.filamentModeAllow')}
                    </label>
                  ))}
                </div>

                <div className="flex flex-wrap gap-2">
                  {rules.length === 0 ? (
                    <span className="text-sm text-bambu-gray">{t('groups.editor.noFilamentRules')}</span>
                  ) : (
                    rules.map((rule) => (
                      <span
                        key={`${rule.match}:${rule.value}`}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded bg-bambu-dark-tertiary text-sm text-white"
                      >
                        <span className="text-bambu-gray">
                          {rule.match === 'material'
                            ? t('groups.editor.filamentMatchMaterial')
                            : t('groups.editor.filamentMatchId')}
                          :
                        </span>
                        {rule.value}
                        <button
                          type="button"
                          aria-label={t('groups.editor.removeFilamentRule', { value: rule.value })}
                          onClick={() => emit({ rules: rules.filter((r) => r !== rule) })}
                          className="text-bambu-gray hover:text-white"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </span>
                    ))
                  )}
                </div>

                {/* The backend treats an empty allow list as "allow nothing" (#3522) */}
                {mode === 'allow' && rules.length === 0 && (
                  <p role="status" className="flex items-center gap-2 text-sm text-yellow-700 dark:text-yellow-400">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    {t('groups.editor.filamentAllowEmptyWarning')}
                  </p>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={match}
                    onChange={(e) => {
                      // A value typed for one kind of rule is not one of the other.
                      setMatch(e.target.value as FilamentRule['match']);
                      setValue('');
                    }}
                    className="px-3 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white"
                  >
                    <option value="material">{t('groups.editor.filamentMatchMaterial')}</option>
                    <option value="filament_id">{t('groups.editor.filamentMatchId')}</option>
                  </select>
                  <input
                    type="text"
                    aria-label={t('groups.editor.filamentRuleValue')}
                    value={value}
                    list={match === 'material' ? 'filament-material-suggestions' : undefined}
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        addRule();
                      }
                    }}
                    placeholder={
                      match === 'material'
                        ? t('groups.editor.filamentRuleMaterialPlaceholder')
                        : t('groups.editor.filamentRuleIdPlaceholder')
                    }
                    maxLength={64}
                    className="px-3 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray w-48"
                  />
                  <datalist id="filament-material-suggestions">
                    {MATERIAL_SUGGESTIONS.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  <Button size="sm" variant="secondary" onClick={addRule} disabled={!value.trim()}>
                    <Plus className="w-4 h-4" />
                    {t('groups.editor.addFilamentRule')}
                  </Button>
                </div>

                <p className="text-xs text-bambu-gray/80">{t('groups.editor.filamentRulesHint')}</p>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
