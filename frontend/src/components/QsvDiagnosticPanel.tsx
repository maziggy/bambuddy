import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  MinusCircle,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api, type QsvDiagnosticStage } from '../api/client';

function StageIcon({ status }: { status: QsvDiagnosticStage['status'] }) {
  if (status === 'ok') {
    return <CheckCircle2 className="w-4 h-4 text-bambu-green shrink-0" />;
  }

  if (status === 'failed') {
    return <XCircle className="w-4 h-4 text-red-500 shrink-0" />;
  }

  return <MinusCircle className="w-4 h-4 text-bambu-gray shrink-0" />;
}

function stageLabel(
  stage: QsvDiagnosticStage,
  t: ReturnType<typeof useTranslation>['t'],
) {
  const labels: Record<QsvDiagnosticStage['name'], string> = {
    ffmpeg: t('settings.qsvDiagnosticStageFfmpeg', 'FFmpeg'),
    render_device: t('settings.qsvDiagnosticStageDevice', 'Intel GPU device'),
    qsv_codecs: t('settings.qsvDiagnosticStageCodecs', 'Required QSV codecs'),
    qsv_initialization: t(
      'settings.qsvDiagnosticStageInitialization',
      'Quick Sync initialization',
    ),
  };

  return labels[stage.name];
}

function failureMessage(
  code: string | null,
  t: ReturnType<typeof useTranslation>['t'],
) {
  switch (code) {
    case 'ffmpeg_not_found':
      return t(
        'settings.qsvDiagnosticFfmpegMissing',
        'FFmpeg is not installed or is not available in PATH.',
      );

    case 'render_device_missing':
      return t(
        'settings.qsvDiagnosticDeviceMissing',
        '/dev/dri/renderD128 was not found. Pass the Intel GPU render device into the container.',
      );

    case 'render_device_permission_denied':
      return t(
        'settings.qsvDiagnosticPermissionDenied',
        'Bambuddy cannot access /dev/dri/renderD128. Check the render group and container device permissions.',
      );

    case 'h264_qsv_missing':
      return t(
        'settings.qsvDiagnosticDecoderMissing',
        'This FFmpeg build does not provide the h264_qsv decoder.',
      );

    case 'mjpeg_qsv_missing':
      return t(
        'settings.qsvDiagnosticEncoderMissing',
        'This FFmpeg build does not provide the mjpeg_qsv encoder.',
      );

    case 'qsv_codecs_missing':
      return t(
        'settings.qsvDiagnosticCodecsMissing',
        'This FFmpeg build does not provide the required QSV codecs.',
      );

    case 'qsv_initialization_failed':
      return t(
        'settings.qsvDiagnosticInitializationFailed',
        'The Intel GPU was found, but Quick Sync could not be initialized. Check the Intel media driver and oneVPL runtime.',
      );

    case 'diagnostic_timeout':
      return t(
        'settings.qsvDiagnosticTimeout',
        'The Quick Sync diagnostic timed out.',
      );

    default:
      return t(
        'settings.qsvDiagnosticUnknownFailure',
        'Quick Sync is not available. Open the details below for diagnostic information.',
      );
  }
}

export function QsvDiagnosticPanel({ selected }: { selected: boolean }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const diagnostic = useQuery({
    queryKey: ['settings', 'qsv-diagnostic'],
    queryFn: () => api.diagnoseQsv(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  if (diagnostic.isPending) {
    return (
      <div className="mt-3 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark px-3 py-3">
        <div className="flex items-center gap-2 text-xs text-bambu-gray">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t(
            'settings.qsvDiagnosticRunning',
            'Checking Intel Quick Sync availability...',
          )}
        </div>
      </div>
    );
  }

  if (diagnostic.isError || !diagnostic.data) {
    return (
      <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-3">
        <div className="flex items-start gap-2">
          <XCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />

          <div className="min-w-0 flex-1">
            <div className="text-sm text-red-300">
              {t(
                'settings.qsvDiagnosticRequestFailed',
                'Could not run the Quick Sync diagnostic.',
              )}
            </div>

            <div className="text-xs text-bambu-gray mt-1 break-words">
              {(diagnostic.error as Error)?.message}
            </div>
          </div>

          <button
            type="button"
            onClick={() => diagnostic.refetch()}
            className="text-bambu-gray hover:text-white"
            title={t('settings.qsvDiagnosticRetry', 'Check again')}
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>
    );
  }

  const result = diagnostic.data;
  const failedStage = result.stages.find(
    (stage) => stage.status === 'failed',
  );

  return (
    <div
      className={`mt-3 rounded-lg border px-3 py-3 ${
        result.available
          ? 'border-bambu-green/30 bg-bambu-green/10'
          : selected
            ? 'border-amber-500/40 bg-amber-500/10'
            : 'border-red-500/30 bg-red-500/10'
      }`}
    >
      <div className="flex items-start gap-2">
        {result.available ? (
          <CheckCircle2 className="w-4 h-4 text-bambu-green mt-0.5 shrink-0" />
        ) : selected ? (
          <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
        ) : (
          <XCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
        )}

        <div className="min-w-0 flex-1">
          <div
            className={`text-sm ${
              result.available
                ? 'text-bambu-green'
                : selected
                  ? 'text-amber-300'
                  : 'text-red-300'
            }`}
          >
            {result.available
              ? t(
                  'settings.qsvDiagnosticAvailable',
                  'Intel Quick Sync is available',
                )
              : t(
                  'settings.qsvDiagnosticUnavailable',
                  'Intel Quick Sync is unavailable',
                )}
          </div>

          <div className="text-xs text-bambu-gray mt-1">
            {result.available
              ? t(
                  'settings.qsvDiagnosticAvailableDescription',
                  'The Intel GPU, FFmpeg codecs and hardware initialization were checked successfully.',
                )
              : failureMessage(
                  failedStage?.code ?? result.summary_code,
                  t,
                )}
          </div>
        </div>

        <button
          type="button"
          onClick={() => diagnostic.refetch()}
          disabled={diagnostic.isFetching}
          className="text-bambu-gray hover:text-white disabled:opacity-50"
          title={t('settings.qsvDiagnosticRetry', 'Check again')}
        >
          <RefreshCw
            className={`w-4 h-4 ${
              diagnostic.isFetching ? 'animate-spin' : ''
            }`}
          />
        </button>
      </div>

      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="mt-2 flex items-center gap-1 text-xs text-bambu-gray hover:text-white"
      >
        {expanded ? (
          <ChevronUp className="w-3.5 h-3.5" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5" />
        )}

        {expanded
          ? t('settings.qsvDiagnosticHideDetails', 'Hide details')
          : t('settings.qsvDiagnosticShowDetails', 'Show details')}
      </button>

      {expanded && (
        <div className="mt-2 space-y-1.5">
          {result.stages.map((stage) => (
            <div
              key={stage.name}
              className="flex items-start gap-2 rounded bg-bambu-dark-secondary px-2.5 py-2"
            >
              <StageIcon status={stage.status} />

              <div className="min-w-0 flex-1">
                <div className="text-xs text-white">
                  {stageLabel(stage, t)}
                </div>

                {stage.detail && (
                  <div className="text-[11px] text-bambu-gray font-mono break-all mt-0.5">
                    {stage.detail}
                  </div>
                )}

                {stage.code && (
                  <div className="text-[11px] text-bambu-gray font-mono mt-0.5">
                    {stage.code}
                  </div>
                )}
              </div>

              <div className="text-[11px] text-bambu-gray tabular-nums shrink-0">
                {stage.duration_ms} ms
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
