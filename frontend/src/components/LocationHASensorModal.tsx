import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Gauge, Loader2, Plus, Save, Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { HADisplayEntity, LocationHASensor, StorageLocation } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { LocationsModal } from './LocationsModal';
import { useToast } from '../contexts/ToastContext';
import { loadLocationSensorDefaults } from '../utils/locationSensorDefaults';
import { HA_SENSOR_BINARY_LABELS } from '../utils/haSensorDisplay';

interface Props {
  sensor?: LocationHASensor | null;
  locations: StorageLocation[];
  onClose: () => void;
}

type SensorCategory = 'temperature' | 'humidity' | 'battery';
// Doubles as the picker's filter (see `entities` below): an entity with no
// category cannot be bound to a location at all.
//
// "moisture" is deliberately absent. It is Home Assistant's binary wet/dry
// class, not a humidity percentage, and mapping it here made it a humidity
// sensor everywhere downstream — it landed in the percent-formatted humidity
// column rendering "wet", blocked a real humidity sensor on the same location
// via the one-per-category rule, and could never take the seeded thresholds
// because the schema rejects alert_above/alert_below for kind="binary".
// A storage location wants a hygrometer, not a leak detector.
function categoryFor(deviceClass: string | null): SensorCategory | null {
  if (deviceClass === 'temperature') return 'temperature';
  if (deviceClass === 'humidity') return 'humidity';
  if (deviceClass === 'battery') return 'battery';
  return null;
}

const CATEGORY_SUFFIXES: Record<SensorCategory, string> = {
  temperature: 'temperature',
  humidity: 'humidity',
  battery: 'battery',
};

function findSiblingEntities(
  entityId: string,
  category: SensorCategory,
  candidates: HADisplayEntity[]
): HADisplayEntity[] {
  const suffix = CATEGORY_SUFFIXES[category];
  const lower = entityId.toLowerCase();
  if (!lower.endsWith(suffix)) return [];
  const prefix = entityId.slice(0, entityId.length - suffix.length);
  const siblings: HADisplayEntity[] = [];
  (Object.keys(CATEGORY_SUFFIXES) as SensorCategory[]).forEach((otherCategory) => {
    if (otherCategory === category) return;
    const candidateId = `${prefix}${CATEGORY_SUFFIXES[otherCategory]}`.toLowerCase();
    const match = candidates.find((c) => c.entity_id.toLowerCase() === candidateId);
    if (match) siblings.push(match);
  });
  return siblings;
}

export function LocationHASensorModal({ sensor, locations, onClose }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const isEditing = !!sensor;

  const [locationId, setLocationId] = useState<number | ''>(sensor?.location_id ?? locations[0]?.id ?? '');
  const [entityId, setEntityId] = useState(sensor?.entity_id ?? '');
  const [kind, setKind] = useState<'binary' | 'numeric'>(sensor?.kind ?? 'numeric');
  const [deviceClass, setDeviceClass] = useState<string | null>(sensor?.device_class ?? null);
  const [unit, setUnit] = useState<string | null>(sensor?.unit ?? null);
  const [name, setName] = useState(sensor?.name ?? '');
  // Tracks the last name we auto-filled (or the initial saved name), so
  // switching to a different entity can follow along with the new friendly
  // name — but only while the field still holds what we put there. A name
  // the user typed themselves is never overwritten by an entity change.
  const autoFilledNameRef = useRef(sensor?.name ?? '');
  const [alertState, setAlertState] = useState<'on' | 'off' | ''>(sensor?.alert_state ?? '');
  const [alertAbove, setAlertAbove] = useState(sensor?.alert_above?.toString() ?? '');
  const [alertBelow, setAlertBelow] = useState(sensor?.alert_below?.toString() ?? '');
  const [notifyOnAlert, setNotifyOnAlert] = useState(sensor?.notify_on_alert ?? false);
  const [showOnCard, setShowOnCard] = useState(sensor?.show_on_card ?? true);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [showAddLocationModal, setShowAddLocationModal] = useState(false);

  const [showOverwriteConfirm, setShowOverwriteConfirm] = useState(false);
  const [overwriteTarget, setOverwriteTarget] = useState<LocationHASensor | null>(null);

  const [showAutoAddConfirm, setShowAutoAddConfirm] = useState(false);
  const [autoAddCandidates, setAutoAddCandidates] = useState<HADisplayEntity[]>([]);
  const [autoAddSelected, setAutoAddSelected] = useState<Record<string, boolean>>({});

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });
  const haConfigured = !!(settings?.ha_enabled && settings?.ha_url && settings?.ha_token);

  const { data: rawEntities, isLoading: entitiesLoading, error: entitiesError } = useQuery({
    queryKey: ['bindableLocationHAEntities'],
    queryFn: () => api.getBindableLocationHAEntities(),
    enabled: haConfigured,
  });

  const entities = useMemo(
    () => (rawEntities ?? []).filter((e) => categoryFor(e.device_class) !== null),
    [rawEntities]
  );

  const { data: allLocationSensors } = useQuery({
    queryKey: ['locationHaSensors'],
    queryFn: () => api.getLocationHASensors(),
  });

  const selected = entities.find((e) => e.entity_id === entityId);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const all = entities ?? [];
    const matches = needle
      ? all.filter((e) => e.entity_id.toLowerCase().includes(needle) || e.friendly_name.toLowerCase().includes(needle))
      : all;
    if (!selected || matches[0]?.entity_id === selected.entity_id) return matches;
    return [selected, ...matches.filter((e) => e.entity_id !== selected.entity_id)];
  }, [entities, search, selected]);

  const selectEntity = (entity: HADisplayEntity) => {
    setEntityId(entity.entity_id);
    setDeviceClass(entity.device_class);
    // Sliced like the name below: the unit is snapshotted from Home Assistant,
    // not typed by the user, so an oversized one must not come back as a 422
    // on a field the form never showed them. The column is String(16).
    setUnit(entity.unit_of_measurement?.slice(0, 16) ?? null);
    const nextKind = entity.domain === 'binary_sensor' ? 'binary' : 'numeric';
    setKind(nextKind);
    if (nextKind === 'numeric') setAlertState('');
    else {
      setAlertAbove('');
      setAlertBelow('');
    }
    // Sliced to the column width: Home Assistant friendly names have no length
    // limit, and a long one would come back as a Pydantic error on a field the
    // user did not type into. Follows the entity picker as long as the name
    // still matches what we last auto-filled — a name the user typed
    // themselves is left alone even when they pick a different entity.
    if (name === autoFilledNameRef.current) {
      const nextName = entity.friendly_name.slice(0, 100);
      setName(nextName);
      autoFilledNameRef.current = nextName;
    }

    if (!isEditing) {
      const category = categoryFor(entity.device_class);
      if (category) {
        const defaults = loadLocationSensorDefaults(settings?.location_sensor_alert_defaults)[category];
        if (nextKind === 'numeric') {
          setAlertAbove(category === 'battery' ? '' : defaults.alertAbove);
          setAlertBelow(defaults.alertBelow);
        }
        setNotifyOnAlert(defaults.notifyOnAlert);
        setShowOnCard(defaults.showOnCard);
      }
    }
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['locationHaSensors'] });
    queryClient.invalidateQueries({ queryKey: ['locationHaSensorReadings'] });
  };

  const buildPrimaryPayload = () => ({
    name: name.trim(),
    entity_id: entityId,
    kind,
    device_class: deviceClass,
    unit,
    alert_state: kind === 'binary' && alertState ? alertState : null,
    alert_above:
      kind === 'numeric' && categoryFor(deviceClass) !== 'battery' && alertAbove !== '' ? Number(alertAbove) : null,
    alert_below: kind === 'numeric' && alertBelow !== '' ? Number(alertBelow) : null,
    notify_on_alert: notifyOnAlert,
    show_on_card: showOnCard,
  });

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = buildPrimaryPayload();
      return isEditing
        ? api.updateLocationHASensor(sensor.id, payload)
        : api.createLocationHASensor({ ...payload, location_id: Number(locationId) });
    },
    onSuccess: () => {
      invalidate();
      showToast(isEditing ? t('locationHaSensors.toast.updated') : t('locationHaSensors.toast.created'), 'success');
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const autoAddMutation = useMutation({
    mutationFn: async () => {
      const targetLocationId = Number(locationId);
      await api.createLocationHASensor({ ...buildPrimaryPayload(), location_id: targetLocationId });
      const defaults = loadLocationSensorDefaults(settings?.location_sensor_alert_defaults);
      const chosen = autoAddCandidates.filter((entity) => autoAddSelected[entity.entity_id]);
      for (const entity of chosen) {
        const categoryDefaults = categoryFor(entity.device_class);
        const d = categoryDefaults ? defaults[categoryDefaults] : null;
        await api.createLocationHASensor({
          name: entity.friendly_name.slice(0, 100),
          entity_id: entity.entity_id,
          kind: entity.domain === 'binary_sensor' ? 'binary' : 'numeric',
          device_class: entity.device_class,
          unit: entity.unit_of_measurement?.slice(0, 16) ?? null,
          alert_state: null,
          alert_above: categoryDefaults !== 'battery' && d && d.alertAbove !== '' ? Number(d.alertAbove) : null,
          alert_below: d && d.alertBelow !== '' ? Number(d.alertBelow) : null,
          notify_on_alert: d?.notifyOnAlert ?? false,
          show_on_card: d?.showOnCard ?? showOnCard,
          location_id: targetLocationId,
        });
      }
      return chosen;
    },
    onSuccess: (chosen) => {
      invalidate();
      setShowAutoAddConfirm(false);
      if (chosen.length > 0) {
        showToast(t('locationHaSensors.autoAdd.added', { names: chosen.map((e) => e.entity_id).join(', ') }), 'success');
      } else {
        showToast(t('locationHaSensors.toast.created'), 'success');
      }
      onClose();
    },
    onError: (err: Error) => {
      invalidate();
      setShowAutoAddConfirm(false);
      setError(err.message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteLocationHASensor(sensor!.id),
    onSuccess: () => {
      invalidate();
      showToast(t('locationHaSensors.toast.deleted'), 'success');
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const overwriteMutation = useMutation({
    // PATCH the existing row onto the new entity instead of deleting it and
    // creating a replacement: a delete-then-create left a window where, if
    // the create failed, the old binding was already gone and nothing had
    // taken its place. A single PATCH either lands or leaves the original
    // binding untouched.
    mutationFn: () => api.updateLocationHASensor(overwriteTarget!.id, buildPrimaryPayload()),
    onSuccess: () => {
      invalidate();
      setShowOverwriteConfirm(false);
      setOverwriteTarget(null);
      showToast(t('locationHaSensors.toast.updated'), 'success');
      onClose();
    },
    onError: (err: Error) => {
      setShowOverwriteConfirm(false);
      setOverwriteTarget(null);
      setError(err.message);
    },
  });

  const hasAlertCondition = kind === 'binary' ? alertState !== '' : alertAbove !== '' || alertBelow !== '';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!entityId) return setError(t('haSensors.error.pickEntity'));
    if (!name.trim()) return setError(t('haSensors.error.nameRequired'));
    if (locationId === '') return setError(t('locationHaSensors.error.locationRequired'));
    if (notifyOnAlert && !hasAlertCondition) {
      return setError(t('haSensors.error.alertRequired'));
    }

    if (!isEditing) {
      const locationSensors = (allLocationSensors ?? []).filter((s) => s.location_id === Number(locationId));
      const category = categoryFor(deviceClass);

      if (category) {
        const conflicting = locationSensors.find((s) => categoryFor(s.device_class) === category);
        if (conflicting) {
          setOverwriteTarget(conflicting);
          setShowOverwriteConfirm(true);
          return;
        }
      }

      if (locationSensors.length === 0 && category) {
        const siblings = findSiblingEntities(entityId, category, entities);
        if (siblings.length > 0) {
          setAutoAddCandidates(siblings);
          setAutoAddSelected(Object.fromEntries(siblings.map((s) => [s.entity_id, true])));
          setShowAutoAddConfirm(true);
          return;
        }
        showToast(t('locationHaSensors.autoAdd.noneFound'), 'info');
      }
    }

    saveMutation.mutate();
  };

  const alertLabels = HA_SENSOR_BINARY_LABELS[deviceClass ?? ''];
  const stateLabel = (which: 'on' | 'off') => {
    const key = alertLabels?.[which] ?? which;
    return t(`haSensors.states.${key}`, { defaultValue: key });
  };

  const isPending =
    saveMutation.isPending || deleteMutation.isPending || overwriteMutation.isPending || autoAddMutation.isPending;
  const currentLocation = locations.find((l) => l.id === Number(locationId));

  // Escape closes the modal, but not while a mutation is mid-flight — a
  // stray keypress landing between the overwrite PATCH's dispatch and its
  // response must not drop the user out with an orphaned request.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isPending) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, isPending]);

  return (
    <>
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={() => {
        if (!isPending) onClose();
      }}
    >
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
              {isEditing ? t('locationHaSensors.editTitle') : t('locationHaSensors.addTitle')}
            </h2>
          </div>
          <button
            onClick={onClose}
            disabled={isPending}
            className="text-bambu-gray hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-100 dark:bg-red-500/20 border border-red-300 dark:border-red-500/50 rounded-lg text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('locationHaSensors.location')}</label>
            {isEditing ? (
              <div className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white">
                {locations.find((l) => l.id === sensor.location_id)?.name ?? t('locationHaSensors.unknownLocation')}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <select
                  value={locationId}
                  onChange={(e) => setLocationId(e.target.value === '' ? '' : Number(e.target.value))}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
                >
                  {locations.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setShowAddLocationModal(true)}
                  className="p-2 rounded-lg bg-bambu-dark-tertiary hover:bg-bambu-gray-dark text-white transition-colors shrink-0"
                  title={t('locations.add')}
                  aria-label={t('locations.add')}
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

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
              {entityId && !selected && (
                <div className="px-3 py-2 text-sm bg-bambu-green/10 text-bambu-green border-b border-bambu-dark-tertiary">
                  {t('locationHaSensors.currentlyBound', { entity: entityId })}
                </div>
              )}
              {!haConfigured && <div className="p-3 text-sm text-bambu-gray">{t('haSensors.noEntities')}</div>}
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
            <label className="block text-sm text-bambu-gray mb-1" htmlFor="location-ha-sensor-name">
              {t('haSensors.name')}
            </label>
            <input
              id="location-ha-sensor-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white"
            />
          </div>

          {entityId && (
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
              ) : categoryFor(deviceClass) === 'battery' ? (
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
          )}

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={showOnCard}
              onChange={(e) => setShowOnCard(e.target.checked)}
              className="w-4 h-4"
            />
            <span className="text-sm text-white">{t('locationHaSensors.showOnCard')}</span>
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
              <Button type="submit" disabled={isPending || (!haConfigured && !isEditing)}>
                {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {t('common.save')}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
    {showOverwriteConfirm && overwriteTarget && (
      <ConfirmModal
        title={t('locationHaSensors.overwriteConfirm.title')}
        message={t(
          `locationHaSensors.overwriteConfirm.message${
            categoryFor(overwriteTarget.device_class) === 'humidity'
              ? 'Humidity'
              : categoryFor(overwriteTarget.device_class) === 'battery'
                ? 'Battery'
                : 'Temperature'
          }`,
          {
            location: currentLocation?.name ?? '',
            name: overwriteTarget.name,
          }
        )}
        variant="warning"
        overlayZIndex="z-[110]"
        isLoading={overwriteMutation.isPending}
        onConfirm={() => overwriteMutation.mutate()}
        onCancel={() => {
          setShowOverwriteConfirm(false);
          setOverwriteTarget(null);
        }}
      />
    )}
    {showAutoAddConfirm && autoAddCandidates.length > 0 && (
      <ConfirmModal
        title={t('locationHaSensors.autoAdd.confirmTitle')}
        message={t('locationHaSensors.autoAdd.confirmMessage')}
        overlayZIndex="z-[110]"
        isLoading={autoAddMutation.isPending}
        confirmDisabled={!autoAddCandidates.some((e) => autoAddSelected[e.entity_id])}
        onConfirm={() => autoAddMutation.mutate()}
        // "Cancel" here still saves the sensor the user picked — it only
        // skips the siblings — so the button needs to say what it does
        // rather than implying the whole thing is being abandoned.
        cancelText={t('locationHaSensors.autoAdd.onlyThisOne')}
        onCancel={() => {
          setShowAutoAddConfirm(false);
          setAutoAddCandidates([]);
          setAutoAddSelected({});
          saveMutation.mutate();
        }}
      >
        <div className="space-y-2">
          {autoAddCandidates.map((entity) => (
            <label key={entity.entity_id} className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={autoAddSelected[entity.entity_id] ?? true}
                onChange={(e) =>
                  setAutoAddSelected((prev) => ({ ...prev, [entity.entity_id]: e.target.checked }))
                }
                className="w-4 h-4"
              />
              <span className="text-sm text-white">
                {entity.friendly_name}
                <span className="text-bambu-gray"> — {entity.entity_id}</span>
              </span>
            </label>
          ))}
        </div>
      </ConfirmModal>
    )}
    {showAddLocationModal && (
      <LocationsModal
        open={showAddLocationModal}
        onClose={() => setShowAddLocationModal(false)}
        onPickLocation={(id) => {
          setLocationId(id);
          setShowAddLocationModal(false);
        }}
        startCreating
      />
    )}
    </>
  );
}
