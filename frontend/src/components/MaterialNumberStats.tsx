import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { api } from '../api/client';

// Consumption, cost and stock grouped by the internal material number
// (#2870) — the identifier the business actually purchases and costs by,
// unlike brand+material+colour. Data comes from the dedicated aggregate
// endpoint so archived spools' recorded usage still counts.

interface MaterialNumberStatsProps {
  currency: string;
}

function formatGrams(g: number): string {
  if (Math.abs(g) >= 1000) return `${(g / 1000).toFixed(2)} kg`;
  return `${Math.round(g)} g`;
}

export function MaterialNumberStats({ currency }: MaterialNumberStatsProps) {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['material-number-stats'],
    queryFn: api.getMaterialNumberStats,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (isError || !data || data.length === 0) {
    return <p className="text-sm text-bambu-gray py-4">{t('stats.materialNumbers.empty')}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-bambu-gray border-b border-bambu-dark-tertiary">
            <th className="py-2 pr-4 font-medium">{t('inventory.materialNumber')}</th>
            <th className="py-2 pr-4 font-medium text-right">{t('stats.materialNumbers.spools')}</th>
            <th className="py-2 pr-4 font-medium text-right">{t('stats.materialNumbers.remaining')}</th>
            <th className="py-2 pr-4 font-medium text-right">{t('stats.materialNumbers.consumed')}</th>
            <th className="py-2 font-medium text-right">{t('stats.materialNumbers.cost')}</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.material_number} className="border-b border-bambu-dark-tertiary/50 last:border-b-0">
              <td className="py-2 pr-4 text-white font-medium">{row.material_number}</td>
              <td className="py-2 pr-4 text-bambu-gray text-right">{row.spool_count}</td>
              <td className="py-2 pr-4 text-bambu-gray text-right">{formatGrams(row.remaining_g)}</td>
              <td className="py-2 pr-4 text-bambu-gray text-right">{formatGrams(row.consumed_g)}</td>
              <td className="py-2 text-bambu-gray text-right">
                {currency} {row.cost.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
