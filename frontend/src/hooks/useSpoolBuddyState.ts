import { useEffect, useReducer, useCallback } from 'react';

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
  // Gradient stops and surface effect, so the kiosk disc can paint what the
  // Filament page paints (#3033). Optional: an older backend omits them.
  extra_colors?: string | null;
  effect_type?: string | null;
  brand: string | null;
  label_weight: number;
  core_weight: number;
  weight_used: number;
}

// What an unknown Bambu tag's own blocks say about the roll, resolved by the
// backend the way AMS auto-creation resolves a tray (#2685). Quick Add creates
// the spool from it. Absent for non-Bambu tags and older daemons.
export interface UnknownTagData {
  material: string;
  subtype: string | null;
  color_name: string | null;
  rgba: string | null;
  extra_colors: string | null;
  effect_type: string | null;
  brand: string;
  label_weight: number;
  slicer_filament: string | null;
  slicer_filament_name: string | null;
  nozzle_temp_min: number | null;
  nozzle_temp_max: number | null;
  core_weight: number;
  core_weight_catalog_id: number | null;
  variant_id: string | null;
  diameter: number | null;
  drying_temp: number | null;
  drying_time: number | null;
}

export interface SpoolBuddyState {
  weight: number | null;
  weightStable: boolean;
  rawAdc: number | null;
  matchedSpool: MatchedSpool | null;
  unknownTagUid: string | null;
  unknownTrayUuid: string | null;
  unknownTagData: UnknownTagData | null;
  deviceOnline: boolean;
  deviceId: string | null;
}

type Action =
  | { type: 'WEIGHT_UPDATE'; weight: number; stable: boolean; rawAdc: number; deviceId: string }
  | { type: 'TAG_MATCHED'; spool: MatchedSpool; deviceId: string }
  | { type: 'UNKNOWN_TAG'; tagUid: string; trayUuid: string | null; tagData: UnknownTagData | null; deviceId: string }
  | { type: 'TAG_REMOVED'; deviceId: string }
  | { type: 'DEVICE_ONLINE'; deviceId: string }
  | { type: 'DEVICE_OFFLINE'; deviceId: string };

const initialState: SpoolBuddyState = {
  weight: null,
  weightStable: false,
  rawAdc: null,
  matchedSpool: null,
  unknownTagUid: null,
  unknownTrayUuid: null,
  unknownTagData: null,
  deviceOnline: false,
  deviceId: null,
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
        unknownTagData: null,
        deviceId: action.deviceId,
      };
    case 'UNKNOWN_TAG':
      return {
        ...state,
        matchedSpool: null,
        unknownTagUid: action.tagUid,
        unknownTrayUuid: action.trayUuid ?? null,
        unknownTagData: action.tagData,
        deviceId: action.deviceId,
      };
    case 'TAG_REMOVED':
      return {
        ...state,
        matchedSpool: null,
        unknownTagUid: null,
        unknownTrayUuid: null,
        unknownTagData: null,
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
      weight: detail.weight_grams,
      stable: detail.stable ?? false,
      rawAdc: detail.raw_adc ?? null,
      deviceId: detail.device_id ?? '',
    });
  }, []);

  const handleTagMatched = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    const spool = detail.spool;
    if (spool) {
      dispatch({
        type: 'TAG_MATCHED',
        spool: {
          id: spool.id,
          tag_uid: detail.tag_uid ?? '',
          material: spool.material ?? '',
          subtype: spool.subtype ?? null,
          color_name: spool.color_name ?? null,
          color_name_is_synthesized: spool.color_name_is_synthesized ?? false,
          rgba: spool.rgba ?? null,
          extra_colors: spool.extra_colors ?? null,
          effect_type: spool.effect_type ?? null,
          brand: spool.brand ?? null,
          label_weight: spool.label_weight ?? 0,
          core_weight: spool.core_weight ?? 0,
          weight_used: spool.weight_used ?? 0,
        },
        deviceId: detail.device_id ?? '',
      });
    }
  }, []);

  const handleUnknownTag = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'UNKNOWN_TAG',
      tagUid: detail.tag_uid ?? '',
      trayUuid: detail.tray_uuid ?? null,
      tagData: detail.tag_data ?? null,
      deviceId: detail.device_id ?? '',
    });
  }, []);

  const handleTagRemoved = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'TAG_REMOVED',
      deviceId: detail.device_id ?? '',
    });
  }, []);

  const handleOnline = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'DEVICE_ONLINE',
      deviceId: detail.device_id ?? '',
    });
  }, []);

  const handleOffline = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    dispatch({
      type: 'DEVICE_OFFLINE',
      deviceId: detail.device_id ?? '',
    });
  }, []);

  useEffect(() => {
    window.addEventListener('spoolbuddy-weight', handleWeight);
    window.addEventListener('spoolbuddy-tag-matched', handleTagMatched);
    window.addEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
    window.addEventListener('spoolbuddy-tag-removed', handleTagRemoved);
    window.addEventListener('spoolbuddy-online', handleOnline);
    window.addEventListener('spoolbuddy-offline', handleOffline);

    return () => {
      window.removeEventListener('spoolbuddy-weight', handleWeight);
      window.removeEventListener('spoolbuddy-tag-matched', handleTagMatched);
      window.removeEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
      window.removeEventListener('spoolbuddy-tag-removed', handleTagRemoved);
      window.removeEventListener('spoolbuddy-online', handleOnline);
      window.removeEventListener('spoolbuddy-offline', handleOffline);
    };
  }, [handleWeight, handleTagMatched, handleUnknownTag, handleTagRemoved, handleOnline, handleOffline]);

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
  };
}
