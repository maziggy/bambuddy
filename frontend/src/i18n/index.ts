import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

// Import translations directly for bundling
import en from './locales/en';
import de from './locales/de';
import es from './locales/es';
import fr from './locales/fr';
import ja from './locales/ja';
import it from './locales/it';
import ko from './locales/ko';
import ptBR from './locales/pt-BR';
import zhCN from './locales/zh-CN';
import zhTW from './locales/zh-TW';
import tr from './locales/tr';

const resources = {
  en: { translation: en },
  de: { translation: de },
  es: { translation: es },
  fr: { translation: fr },
  ja: { translation: ja },
  it: { translation: it },
  ko: { translation: ko },
  'pt-BR': { translation: ptBR },
  'zh-CN': { translation: zhCN },
  'zh-TW': { translation: zhTW },
  tr: { translation: tr },
};

const SUPPORTED_LNGS = ['en', 'de', 'es', 'fr', 'ja', 'it', 'ko', 'pt-BR', 'tr', 'zh-CN', 'zh-TW'];
const APPLIANCE_CONSUMED_KEY = 'bambuddy_appliance_locale_consumed';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LNGS,

    detection: {
      // Order of detection methods
      order: ['localStorage', 'navigator', 'htmlTag'],
      // Key to use in localStorage
      lookupLocalStorage: 'bambutrack_language',
      // Cache user language
      caches: ['localStorage'],
    },

    interpolation: {
      escapeValue: false, // React already escapes
    },

    react: {
      useSuspense: false,
    },
  });

/**
 * Bambuddy Appliance hook: on the first SPA load after the firstboot wizard
 * runs, /api/v1/system/appliance returns the locale the user picked. We
 * apply it once (gated by a localStorage flag) and stop. On non-appliance
 * installs the endpoint either 404s or returns nulls — silent no-op.
 *
 * This runs AFTER i18n.init so the LanguageDetector has already populated a
 * default; we override that default exactly once for fresh appliances. The
 * appliance is then "consumed" and the language picker is the only way to
 * change locale going forward (the wizard ran once; future intent comes from
 * the running UI).
 */
function applyApplianceLocale() {
  if (typeof window === 'undefined' || !window.localStorage) return;
  const storage = window.localStorage;
  if (typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') return;
  if (storage.getItem(APPLIANCE_CONSUMED_KEY)) return;

  fetch('/api/v1/system/appliance')
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data || typeof data.locale !== 'string') return;
      if (!SUPPORTED_LNGS.includes(data.locale)) return;
      i18n.changeLanguage(data.locale);
      storage.setItem(APPLIANCE_CONSUMED_KEY, '1');
    })
    .catch(() => {
      // Endpoint absent or unreachable — non-appliance install or dev environment.
      // Leave the detector's choice in place.
    });
}

applyApplianceLocale();

export default i18n;

// Helper to get available languages
export const availableLanguages = [
  { code: 'en', name: 'English', nativeName: 'English' },
  { code: 'de', name: 'German', nativeName: 'Deutsch' },
  { code: 'es', name: 'Spanish', nativeName: 'Español' },
  { code: 'fr', name: 'French', nativeName: 'Français' },
  { code: 'ja', name: 'Japanese', nativeName: '日本語' },
  { code: 'it', name: 'Italian', nativeName: 'Italiano' },
  { code: 'ko', name: 'Korean', nativeName: '한국어' },
  { code: 'pt-BR', name: 'Portuguese (Brazil)', nativeName: 'Português (Brasil)' },
  { code: 'zh-CN', name: 'Chinese (Simplified)', nativeName: '简体中文' },
  { code: 'zh-TW', name: 'Chinese (Traditional)', nativeName: '繁體中文' },
  { code: 'tr', name: 'Turkish', nativeName: 'Türkçe' },
];
