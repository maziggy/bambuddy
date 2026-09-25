import { useEffect, useReducer, useCallback } from 'react';
import type { BarcodeLookupResult } from '../api/client';

export interface MatchedSpool {
  id: number;
  tag_uid: string;
  material: string;
  subtype: string | null;
  color_name: string | null;
  // True when the backend had no stored colour name and put the subtype there
  // instead — Spoolman-backed inventory only, which has no such field (#3090).
  color_name_is_synthesized?: boolean;
  rgba: string | null;
  brand: string | null;
  label_weight: number;
  core_weight: number;
  weight_used: number;
  /** How the roll was purchased (refill coil vs boxed with a spool). */
  bought_as_refill?: boolean;
}

export type { LinkedCode } from '../api/client';

/**
 * A hardware scan surfaced over WS. The filament fields are the REST lookup
 * shape (`BarcodeLookupResult`) by design — the backend builds the WS payload
 * from the same schema — plus scan-delivery metadata.
 */
export type ScannedBarcode = Omit<BarcodeLookupResult, 'enabled' | 'source'> & {
  source: BarcodeLookupResult['source'] | 'parsed';
  kind: string;
  /** AIM symbology family from the hardware scanner (null unless it is
   * configured to transmit AIM IDs). Forwarded on create so the backend
   * routes the code with the same evidence it classified it with. */
  symbology: string | null;
  valid: boolean;
  deviceId: string;
  // Monotonic-ish receipt timestamp (Date.now) so consumers can ignore a
  // stale scan surfaced by a late WS reconnect rather than a fresh trigger.
  receivedAt: number;
};

export interface SpoolBuddyState {
  weight: number | null;
  weightStable: boolean;
  rawAdc: number | null;
  matchedSpool: MatchedSpool | null;
  unknownTagUid: string | null;
  unknownTrayUuid: string | null;
  deviceOnline: boolean;
  deviceId: string | null;
  lastScan: ScannedBarcode | null;
}

type Action =
  | { type: 'WEIGHT_UPDATE'; weight: number; stable: boolean; rawAdc: number; deviceId: string }
  | { type: 'TAG_MATCHED'; spool: MatchedSpool; deviceId: string }
  | { type: 'UNKNOWN_TAG'; tagUid: string; trayUuid: string | null; deviceId: string }
  | { type: 'TAG_REMOVED'; deviceId: string }
  | { type: 'DEVICE_ONLINE'; deviceId: string }
  | { type: 'DEVICE_OFFLINE'; deviceId: string }
  | { type: 'BARCODE_SCANNED'; scan: ScannedBarcode }
  | { type: 'BARCODE_CONSUMED' };

const initialState: SpoolBuddyState = {
  weight: null,
  weightStable: false,
  rawAdc: null,
  matchedSpool: null,
  unknownTagUid: null,
  unknownTrayUuid: null,
  deviceOnline: false,
  deviceId: null,
  lastScan: null,
};

function reducer(state: SpoolBuddyState, action: Action): SpoolBuddyState {
  switch (action.type) {
    case 'WEIGHT_UPDATE':
      return {
        ...state,
        weight: action.weight,
        weightStable: action.stable,
        rawAdc: action.rawAdc,
        deviceId: action.deviceId,
        deviceOnline: true,
      };
    case 'TAG_MATCHED':
      return {
        ...state,
        matchedSpool: action.spool,
        unknownTagUid: null,
        unknownTrayUuid: null,
        deviceId: action.deviceId,
      };
    case 'UNKNOWN_TAG':
      return {
        ...state,
        matchedSpool: null,
        unknownTagUid: action.tagUid,
        unknownTrayUuid: action.trayUuid ?? null,
        deviceId: action.deviceId,
      };
    case 'TAG_REMOVED':
      return {
        ...state,
        matchedSpool: null,
        unknownTagUid: null,
        unknownTrayUuid: null,
      };
    case 'DEVICE_ONLINE':
      return {
        ...state,
        deviceOnline: true,
        deviceId: action.deviceId,
      };
    case 'DEVICE_OFFLINE':
      return {
        ...state,
        deviceOnline: false,
        weight: null,
        weightStable: false,
        rawAdc: null,
      };
    case 'BARCODE_SCANNED':
      // Replace-latest: a new scan always supersedes the previous one, so
      // "Rescan" is implicit and only the most recent code is ever acted on.
      return {
        ...state,
        lastScan: action.scan,
        deviceId: action.scan.deviceId || state.deviceId,
        deviceOnline: true,
      };
    case 'BARCODE_CONSUMED':
      return { ...state, lastScan: null };
    default:
      return state;
  }
}

export function useSpoolBuddyState() {
  const [state, dispatch] = useReducer(reducer, initialState);

  const handleWeight = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'WEIGHT_UPDATE',
      weight: detail.weight_grams ?? detail.data?.weight_grams,
      stable: detail.stable ?? detail.data?.stable ?? false,
      rawAdc: detail.raw_adc ?? detail.data?.raw_adc ?? null,
      deviceId: detail.device_id ?? detail.data?.device_id ?? '',
    });
  }, []);

  const handleTagMatched = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    const spool = detail.spool ?? detail.data?.spool;
    if (spool) {
      dispatch({
        type: 'TAG_MATCHED',
        spool: {
          id: spool.id,
          tag_uid: detail.tag_uid ?? detail.data?.tag_uid ?? '',
          material: spool.material ?? '',
          subtype: spool.subtype ?? null,
          color_name: spool.color_name ?? null,
          color_name_is_synthesized: spool.color_name_is_synthesized ?? false,
          rgba: spool.rgba ?? null,
          brand: spool.brand ?? null,
          label_weight: spool.label_weight ?? 0,
          core_weight: spool.core_weight ?? 0,
          weight_used: spool.weight_used ?? 0,
        },
        deviceId: detail.device_id ?? detail.data?.device_id ?? '',
      });
    }
  }, []);

  const handleUnknownTag = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'UNKNOWN_TAG',
      tagUid: detail.tag_uid ?? detail.data?.tag_uid ?? '',
      trayUuid: detail.tray_uuid ?? detail.data?.tray_uuid ?? null,
      deviceId: detail.device_id ?? detail.data?.device_id ?? '',
    });
  }, []);

  const handleTagRemoved = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'TAG_REMOVED',
      deviceId: detail.device_id ?? detail.data?.device_id ?? '',
    });
  }, []);

  const handleOnline = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'DEVICE_ONLINE',
      deviceId: detail.device_id ?? detail.data?.device_id ?? '',
    });
  }, []);

  const handleOffline = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'DEVICE_OFFLINE',
      deviceId: detail.device_id ?? detail.data?.device_id ?? '',
    });
  }, []);

  const handleBarcodeScanned = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail ?? {};
    const d = detail.data ?? detail;
    dispatch({
      type: 'BARCODE_SCANNED',
      scan: {
        barcode: d.barcode ?? '',
        kind: d.kind ?? 'gtin',
        symbology: d.symbology ?? null,
        valid: d.valid ?? false,
        matched: d.matched ?? false,
        source: d.source ?? null,
        material: d.material ?? null,
        brand: d.brand ?? null,
        subtype: d.subtype ?? null,
        color_name: d.color_name ?? null,
        rgba: d.rgba ?? null,
        label_weight: d.label_weight ?? null,
        nozzle_temp_min: d.nozzle_temp_min ?? null,
        nozzle_temp_max: d.nozzle_temp_max ?? null,
        is_refill: d.is_refill ?? false,
        linked_codes: d.linked_codes ?? [],
        deviceId: d.device_id ?? '',
        receivedAt: Date.now(),
      },
    });
  }, []);

  const clearScan = useCallback(() => {
    dispatch({ type: 'BARCODE_CONSUMED' });
  }, []);

  useEffect(() => {
    window.addEventListener('spoolbuddy-weight', handleWeight);
    window.addEventListener('spoolbuddy-tag-matched', handleTagMatched);
    window.addEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
    window.addEventListener('spoolbuddy-tag-removed', handleTagRemoved);
    window.addEventListener('spoolbuddy-online', handleOnline);
    window.addEventListener('spoolbuddy-offline', handleOffline);
    window.addEventListener('spoolbuddy-barcode-scanned', handleBarcodeScanned);

    return () => {
      window.removeEventListener('spoolbuddy-weight', handleWeight);
      window.removeEventListener('spoolbuddy-tag-matched', handleTagMatched);
      window.removeEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
      window.removeEventListener('spoolbuddy-tag-removed', handleTagRemoved);
      window.removeEventListener('spoolbuddy-online', handleOnline);
      window.removeEventListener('spoolbuddy-offline', handleOffline);
      window.removeEventListener('spoolbuddy-barcode-scanned', handleBarcodeScanned);
    };
  }, [handleWeight, handleTagMatched, handleUnknownTag, handleTagRemoved, handleOnline, handleOffline, handleBarcodeScanned]);

  const remainingWeight = state.matchedSpool
    ? Math.max(0, state.matchedSpool.label_weight - state.matchedSpool.weight_used)
    : null;

  const netWeight = state.weight !== null && state.matchedSpool
    ? Math.max(0, state.weight - state.matchedSpool.core_weight)
    : null;

  return {
    ...state,
    remainingWeight,
    netWeight,
    clearScan,
  };
}
