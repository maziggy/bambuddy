import { useTranslation } from 'react-i18next';
import type { CostCenterSummary } from '../../api/client';

interface CostCenterSelectProps {
  costCenters: CostCenterSummary[];
  selectedCostCenterId: number | null;
  onChange: (costCenterId: number | null) => void;
}

export function CostCenterSelect({
  costCenters,
  selectedCostCenterId,
  onChange,
}: CostCenterSelectProps) {
  const { t } = useTranslation();

  if (costCenters.length === 0) return null;

  return (
    <div className="space-y-1">
      <label htmlFor="printCostCenter" className="text-sm text-bambu-gray">
        {t('printModal.costCenter', 'Cost center')}
      </label>
      <select
        id="printCostCenter"
        value={selectedCostCenterId ?? ''}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        className="w-full px-3 py-2 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white focus:outline-none focus:ring-1 focus:ring-bambu-green"
      >
        {costCenters.map((center) => (
          <option key={center.id} value={center.id}>
            {center.name}{center.is_private ? ` (${t('printModal.personalDefault', 'Personal')})` : ''}
          </option>
        ))}
      </select>
    </div>
  );
}
