import { ChevronDown, Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { availableLanguages } from '../i18n/languages';

/**
 * Interface language of this browser (#3623). Available to everyone, with no
 * permission: it only calls i18n.changeLanguage, which the detector caches in
 * localStorage (`bambutrack_language`). It never writes the install-wide
 * `language` setting -- that stays in Settings > General.
 */
export function LanguageSelect({ id, className = '' }: { id: string; className?: string }) {
  const { t, i18n } = useTranslation();
  // i18n.language can carry a region ("de-DE") the list does not have.
  const current =
    availableLanguages.find((l) => l.code === i18n.language)?.code ??
    availableLanguages.find((l) => l.code === i18n.language?.split('-')[0])?.code ??
    'en';

  return (
    <div className={className}>
      <label htmlFor={id} className="flex items-center gap-1 text-xs text-bambu-gray mb-1">
        <Globe className="w-3.5 h-3.5" />
        {t('languageMenu.label')}
      </label>
      <div className="relative">
        <select
          id={id}
          value={current}
          onChange={(e) => {
            void i18n.changeLanguage(e.target.value);
          }}
          className="w-full px-2 py-1.5 pr-8 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
        >
          {availableLanguages.map((lang) => (
            <option key={lang.code} value={lang.code}>
              {lang.nativeName}
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
      </div>
    </div>
  );
}
