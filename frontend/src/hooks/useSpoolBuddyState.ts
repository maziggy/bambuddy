import { useEffect, useReducer, useCallback } from 'react';

export interface MatchedSpool {
  id: number;
  tag_uid: string;
  material: string;
  subtype: string | null;
  color_name: string | null;
  rgba: string | null;
  brand: string | null;
  label_weight: number;
  core_weight: number;
  weight_used: number;
}

export interface SpoolBuddyState {
  weight: number | null;
  weightStable: boolean;
  rawAdc: number | null;
  matchedSpool: MatchedSpool | null;
  unknownTagUid: string | null;
  deviceOnline: boolean;
  deviceId: string | null;
}

type Action =
  | { type: 'WEIGHT_UPDATE'; weight: number; stable: boolean; rawAdc: number; deviceId: string }
  | { type: 'TAG_MATCHED'; spool: MatchedSpool; deviceId: string }
  | { type: 'UNKNOWN_TAG'; tagUid: string; deviceId: string }
  | { type: 'TAG_REMOVED'; deviceId: string }
  | { type: 'DEVICE_ONLINE'; deviceId: string }
  | { type: 'DEVICE_OFFLINE'; deviceId: string };

const initialState: SpoolBuddyState = {
  weight: null,
  weightStable: false,
  rawAdc: null,
  matchedSpool: null,
  unknownTagUid: null,
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
        deviceId: action.deviceId,
      };
    case 'UNKNOWN_TAG':
      return {
        ...state,
        matchedSpool: null,
        unknownTagUid: action.tagUid,
        deviceId: action.deviceId,
      };
    case 'TAG_REMOVED':
      return {
        ...state,
        matchedSpool: null,
        unknownTagUid: null,
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
