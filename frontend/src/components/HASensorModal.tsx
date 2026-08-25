import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Gauge, Loader2, Save, Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { HADisplayEntity, Printer, PrinterHASensor } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { HA_SENSOR_BINARY_LABELS } from '../utils/haSensorDisplay';

/**
 * Bind a Home Assistant entity to a printer, or edit an existing binding
 * (#1148, #448).
 *
 * The entity picker is the load-bearing part: kind, device_class and unit all
 * come from the entity rather than from the user, because getting any of them
 * wrong is a validation error from the backend that nobody could act on.
 */

interface Props {
  sensor?: PrinterHASensor | null;
  printers: Printer[];
  onClose: () => void;
}

export function HASensorModal({ sensor, printers, onClose }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const isEditing = !!sensor;

  const [printerId, setPrinterId] = useState<number | ''>(sensor?.printer_id ?? printers[0]?.id ?? '');
  const [entityId, setEntityId] = useState(sensor?.entity_id ?? '');
  const [kind, setKind] = useState<'binary' | 'numeric'>(sensor?.kind ?? 'binary');
  const [deviceClass, setDeviceClass] = useState<string | null>(sensor?.device_class ?? null);
  const [unit, setUnit] = useState<string | null>(sensor?.unit ?? null);
  const [name, setName] = useState(sensor?.name ?? '');
  const [alertState, setAlertState] = useState<'on' | 'off' | ''>(sensor?.alert_state ?? '');
  const [alertAbove, setAlertAbove] = useState(sensor?.alert_above?.toString() ?? '');
  const [alertBelow, setAlertBelow] = useState(sensor?.alert_below?.toString() ?? '');
  const [showOnCard, setShowOnCard] = useState(sensor?.show_on_printer_card ?? true);
  const [notifyOnAlert, setNotifyOnAlert] = useState(sensor?.notify_on_alert ?? false);
  const [blockPrint, setBlockPrint] = useState(sensor?.block_print ?? false);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Same gate and the same wording as AddSmartPlugModal: without a configured
  // Home Assistant the picker can only return an error, so say why up front
  // instead of showing an empty list.
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });
  const haConfigured = !!(settings?.ha_enabled && settings?.ha_url && settings?.ha_token);

  const { data: entities, isLoading: entitiesLoading, error: entitiesError } = useQuery({
    queryKey: ['bindableHAEntities'],
    queryFn: () => api.getBindableHAEntities(),
    enabled: haConfigured,
  });

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const all = entities ?? [];
    if (!needle) return all;
    return all.filter(
      (e) => e.entity_id.toLowerCase().includes(needle) || e.friendly_name.toLowerCase().includes(needle)
    );
  }, [entities, search]);

  const selectEntity = (entity: HADisplayEntity) => {
    setEntityId(entity.entity_id);
    setDeviceClass(entity.device_class);
    setUnit(entity.unit_of_measurement);
    const nextKind = entity.domain === 'binary_sensor' ? 'binary' : 'numeric';
    setKind(nextKind);
    // Switching kind strands the other kind's alert fields, and the backend
    // rejects a numeric sensor that still carries an alert_state.
    if (nextKind === 'numeric') setAlertState('');
    else {
      setAlertAbove('');
      setAlertBelow('');
    }
    // Sliced to the column width: Home Assistant friendly names have no length
    // limit, and a long one would come back as a Pydantic error on a field the
    // user did not type into.
    if (!name.trim()) setName(entity.friendly_name.slice(0, 100));
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['haSensors'] });
    queryClient.invalidateQueries({ queryKey: ['haSensorReadings'] });
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        name: name.trim(),
        entity_id: entityId,
        kind,
        device_class: deviceClass,
        unit,
        alert_state: kind === 'binary' && alertState ? alertState : null,
        alert_above: kind === 'numeric' && alertAbove !== '' ? Number(alertAbove) : null,
        alert_below: kind === 'numeric' && alertBelow !== '' ? Number(alertBelow) : null,
        block_print: blockPrint,
        notify_on_alert: notifyOnAlert,
        show_on_printer_card: showOnCard,
      };
      return isEditing
        ? api.updateHASensor(sensor.id, payload)
        : api.createHASensor({ ...payload, printer_id: Number(printerId) });
    },
    onSuccess: () => {
      invalidate();
      showToast(isEditing ? t('haSensors.toast.updated') : t('haSensors.toast.created'), 'success');
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteHASensor(sensor!.id),
    onSuccess: () => {
      invalidate();
      showToast(t('haSensors.toast.deleted'), 'success');
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const hasAlertCondition =
    kind === 'binary' ? alertState !== '' : alertAbove !== '' || alertBelow !== '';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!entityId) return setError(t('haSensors.error.pickEntity'));
    if (!name.trim()) return setError(t('haSensors.error.nameRequired'));
    if (printerId === '') return setError(t('haSensors.error.printerRequired'));
    // Mirrors the backend rule, so the user is told before the round trip
    // rather than by a 422.
    if ((blockPrint || notifyOnAlert) && !hasAlertCondition) {
      return setError(t('haSensors.error.alertRequired'));
    }
    saveMutation.mutate();
  };

  const alertLabels = HA_SENSOR_BINARY_LABELS[deviceClass ?? ''];
  const stateLabel = (which: 'on' | 'off') => {
    const key = alertLabels?.[which] ?? which;
    return t(`haSensors.states.${key}`, { defaultValue: key });
  };

  const isPending = saveMutation.isPending || deleteMutation.isPending;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-full bg-bambu-green/20 text-bambu-green">
              <Gauge className="w-5 h-5" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {isEditing ? t('haSensors.editTitle') : t('haSensors.addTitle')}
            </h2>
          </div>
          <button onClick={onClose} className="text-bambu-gray hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-100 dark:bg-red-500/20 border border-red-300 dark:border-red-500/50 rounded-lg text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          {!isEditing && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('haSensors.printer')}</label>
              <select
                value={printerId}
                onChange={(e) => setPrinterId(e.target.value === '' ? '' : Number(e.target.value))}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
              >
                {printers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {!haConfigured && (
            <div className="p-3 bg-yellow-100 dark:bg-yellow-500/20 border border-yellow-400 dark:border-yellow-500/50 rounded-lg text-sm text-yellow-700 dark:text-yellow-400">
              {t('smartPlugs.haNotConfigured')}{' '}
              <span className="font-medium">{t('smartPlugs.haSettingsPath')}</span>
            </div>
          )}

          <div>
            <label className={`block text-sm text-bambu-gray mb-1 ${haConfigured ? '' : 'opacity-50'}`}>
              {t('haSensors.entity')}
            </label>
            {entitiesError && (
              <div className="p-3 mb-2 bg-red-100 dark:bg-red-500/20 rounded-lg text-sm text-red-700 dark:text-red-400">
                {(entitiesError as Error).message}
              </div>
            )}
            <div className="relative mb-2">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('haSensors.searchPlaceholder')}
                disabled={!haConfigured}
                className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>
            <div
              className={`max-h-44 overflow-y-auto rounded-lg border border-bambu-dark-tertiary ${
                haConfigured ? '' : 'opacity-50'
              }`}
            >
              {!haConfigured && (
                <div className="p-3 text-sm text-bambu-gray">{t('haSensors.noEntities')}</div>
              )}
              {haConfigured && entitiesLoading && (
                <div className="flex items-center gap-2 p-3 text-sm text-bambu-gray">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('common.loading')}
                </div>
              )}
              {haConfigured && !entitiesLoading && filtered.length === 0 && (
                <div className="p-3 text-sm text-bambu-gray">{t('haSensors.noEntities')}</div>
              )}
              {haConfigured &&
                !entitiesLoading &&
                filtered.map((entity) => (
                  <button
                    key={entity.entity_id}
                    type="button"
                    onClick={() => selectEntity(entity)}
                    className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                      entity.entity_id === entityId
                        ? 'bg-bambu-green/20 text-bambu-green'
                        : 'text-white hover:bg-bambu-dark'
                    }`}
                  >
                    <div className="font-medium">{entity.friendly_name}</div>
                    <div className="text-xs text-bambu-gray">
                      {entity.entity_id}
                      {entity.state !== null && ` — ${entity.state}`}
                      {entity.unit_of_measurement ? ` ${entity.unit_of_measurement}` : ''}
                    </div>
                  </button>
                ))}
            </div>
          </div>

          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('haSensors.name')}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
            />
          </div>

          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('haSensors.alertWhen')}</label>
            {kind === 'binary' ? (
              <select
                value={alertState}
                onChange={(e) => setAlertState(e.target.value as 'on' | 'off' | '')}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
              >
                <option value="">{t('haSensors.alertNever')}</option>
                <option value="on">{stateLabel('on')}</option>
                <option value="off">{stateLabel('off')}</option>
              </select>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className="block text-xs text-bambu-gray mb-1">
                    {t('haSensors.alertAbove')} {unit ?? ''}
                  </span>
                  <input
                    type="number"
                    step="any"
                    value={alertAbove}
                    onChange={(e) => setAlertAbove(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
                  />
                </div>
                <div>
                  <span className="block text-xs text-bambu-gray mb-1">
                    {t('haSensors.alertBelow')} {unit ?? ''}
                  </span>
                  <input
                    type="number"
                    step="any"
                    value={alertBelow}
                    onChange={(e) => setAlertBelow(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
                  />
                </div>
              </div>
            )}
            <p className="mt-1 text-xs text-bambu-gray">{t('haSensors.alertHint')}</p>
          </div>

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={showOnCard}
              onChange={(e) => setShowOnCard(e.target.checked)}
              className="w-4 h-4"
            />
            <span className="text-sm text-white">{t('haSensors.showOnCard')}</span>
          </label>

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={notifyOnAlert}
              onChange={(e) => setNotifyOnAlert(e.target.checked)}
              disabled={!hasAlertCondition}
              className="w-4 h-4"
            />
            <span className={`text-sm ${hasAlertCondition ? 'text-white' : 'text-bambu-gray'}`}>
              {t('haSensors.notifyOnAlert')}
            </span>
          </label>

          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={blockPrint}
              onChange={(e) => setBlockPrint(e.target.checked)}
              disabled={!hasAlertCondition}
              className="w-4 h-4 mt-0.5"
            />
            <span>
              <span className={`block text-sm ${hasAlertCondition ? 'text-white' : 'text-bambu-gray'}`}>
                {t('haSensors.blockPrint')}
              </span>
              <span className="block text-xs text-bambu-gray">{t('haSensors.blockPrintHint')}</span>
            </span>
          </label>

          <div className="flex items-center justify-between pt-2">
            {isEditing ? (
              <Button type="button" variant="danger" onClick={() => deleteMutation.mutate()} disabled={isPending}>
                {t('common.delete')}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex items-center gap-2">
              <Button type="button" variant="secondary" onClick={onClose} disabled={isPending}>
                {t('common.cancel')}
              </Button>
              {/* An unconfigured Home Assistant leaves nothing to bind to.
                  Editing an existing sensor still saves — its alert rule and
                  card visibility are Bambuddy's own settings and do not need
                  Home Assistant to be reachable to change. */}
              <Button type="submit" disabled={isPending || (!haConfigured && !isEditing)}>
                {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('common.save')}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
