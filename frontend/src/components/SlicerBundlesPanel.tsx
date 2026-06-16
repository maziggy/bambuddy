import { useTranslation } from 'react-i18next';
import { Package } from 'lucide-react';
import { Card, CardContent, CardHeader } from './Card';

// Static notice replacing the former Printer Preset Bundle import UI.
// Removed in #1712: BambuStudio's bundle export only includes user-
// customised presets, so users who exported a bundle ended up with no
// process presets to slice with (BS doesn't export system processes).
// Users with custom presets now route through Single Preset Import or
// cloud sync; the standard tier on the sidecar already provides every
// stock preset for slicing.
export function SlicerBundlesPanel() {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <Package className="w-4 h-4 text-bambu-gray" />
          {t('settings.slicerBundlesRemoved.title', {
            defaultValue: 'Slicer Bundles (removed)',
          })}
        </h3>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-bambu-gray">
          {t('settings.slicerBundlesRemoved.description', {
            defaultValue:
              'Printer Preset Bundle (.bbscfg) import was removed. BambuStudio\'s bundle export only includes user-customised presets, so the import never delivered standard processes / filaments and slicing fell back to embedded settings.',
          })}
        </p>
        <p className="text-sm text-bambu-gray">
          {t('settings.slicerBundlesRemoved.alternatives', {
            defaultValue:
              'Use Single Preset Import for individual customs, or sync via Bambu Cloud / Orca Cloud. Stock presets come from the slicer sidecar automatically.',
          })}
        </p>
        <p className="text-sm text-bambu-gray">
          {t('settings.slicerBundlesRemoved.lookupOrder', {
            defaultValue:
              'Slice-time preset lookup order: 1) Imported (local), 2) Orca Cloud, 3) Bambu Cloud, 4) Standard (sidecar fallback).',
          })}
        </p>
      </CardContent>
    </Card>
  );
}
