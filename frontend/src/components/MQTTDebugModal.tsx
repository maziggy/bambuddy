import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Play, Square, Trash2, RefreshCw, ArrowDown, ArrowUp, Search } from 'lucide-react';
import { api, type MQTTLogEntry } from '../api/client';
import { Button } from './Button';
import { useState, useEffect, useRef, useMemo } from 'react';

interface MQTTDebugModalProps {
  printerId: number;
  printerName: string;
  onClose: () => void;
}

export function MQTTDebugModal({ printerId, printerName, onClose }: MQTTDebugModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [autoScroll, setAutoScroll] = useState(true);
  const [expandedLogs, setExpandedLogs] = useState<Set<number>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [directionFilter, setDirectionFilter] = useState<'all' | 'in' | 'out'>('all');
  const logContainerRef = useRef<HTMLDivElement>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['mqtt-logs', printerId],
    queryFn: () => api.getMQTTLogs(printerId),
    refetchInterval: 1000, // Poll every second when logging is enabled
  });

  const enableMutation = useMutation({
    mutationFn: () => api.enableMQTTLogging(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mqtt-logs', printerId] });
    },
  });

  const disableMutation = useMutation({
    mutationFn: () => api.disableMQTTLogging(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mqtt-logs', printerId] });
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => api.clearMQTTLogs(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mqtt-logs', printerId] });
    },
  });

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [data?.logs, autoScroll]);

  const toggleExpand = (index: number) => {
    setExpandedLogs((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(index)) {
        newSet.delete(index);
      } else {
        newSet.add(index);
      }
      return newSet;
    });
  };

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', { hour12: false, fractionalSecondDigits: 3 });
  };

  const formatPayload = (payload: unknown, expanded: boolean): string => {
    if (payload === undefined || payload === null) {
      return '<empty>';
    }
    // If payload is already a string, parse it first to format nicely
    const obj = typeof payload === 'string' ? JSON.parse(payload) : payload;
    const json = JSON.stringify(obj, null, expanded ? 2 : 0);
    if (!expanded && json.length > 100) {
      return json.substring(0, 100) + '...';
    }
    return json;
  };

  const loggingEnabled = data?.logging_enabled ?? false;
  const logs = useMemo(() => data?.logs ?? [], [data?.logs]);

  // Filter logs based on search query and direction filter
  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      // Direction filter
      if (directionFilter !== 'all' && log.direction !== directionFilter) {
        return false;
      }
      // Search filter
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const topicMatch = log.topic.toLowerCase().includes(query);
        const payloadStr = JSON.stringify(log.payload).toLowerCase();
        const payloadMatch = payloadStr.includes(query);
        return topicMatch || payloadMatch;
      }
      return true;
    });
  }, [logs, searchQuery, directionFilter]);

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg max-w-4xl w-full max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div>
            <h2 className="text-lg font-semibold text-white">{t('mqttDebug.title')}</h2>
            <p className="text-sm text-bambu-gray">{printerName}</p>
          </div>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Controls */}
        <div className="flex flex-col gap-2 p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            {loggingEnabled ? (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => disableMutation.mutate()}
                disabled={disableMutation.isPending}
              >
                <Square className="w-4 h-4" />
                {t('mqttDebug.stopLogging')}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => enableMutation.mutate()}
                disabled={enableMutation.isPending}
              >
                <Play className="w-4 h-4" />
                {t('mqttDebug.startLogging')}
              </Button>
            )}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => clearMutation.mutate()}
              disabled={clearMutation.isPending || logs.length === 0}
            >
              <Trash2 className="w-4 h-4" />
              {t('mqttDebug.clearLog')}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => refetch()}
              disabled={isLoading}
            >
              <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
            <div className="flex-1" />
            <label className="flex items-center gap-2 text-sm text-bambu-gray cursor-pointer">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded border-bambu-dark-tertiary"
              />
              Auto-scroll
            </label>
            <span className="text-sm text-bambu-gray">
              {filteredLogs.length}/{logs.length}
            </span>
          </div>

          {/* Search and Filter Row */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
              <input
                type="text"
                placeholder={t('mqttDebug.searchPlaceholder')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
            <div className="flex items-center gap-1 bg-bambu-dark rounded border border-bambu-dark-tertiary">
              <button
                onClick={() => setDirectionFilter('all')}
                className={`px-2 py-1.5 text-xs rounded-l transition-colors ${
                  directionFilter === 'all'
                    ? 'bg-bambu-green text-white'
                    : 'text-bambu-gray hover:text-white'
                }`}
              >
                {t('mqttDebug.all')}
              </button>
              <button
                onClick={() => setDirectionFilter('in')}
                className={`px-2 py-1.5 text-xs transition-colors flex items-center gap-1 ${
                  directionFilter === 'in'
                    ? 'bg-blue-500 text-white'
                    : 'text-bambu-gray hover:text-white'
                }`}
              >
                <ArrowDown className="w-3 h-3" />
                {t('mqttDebug.incoming')}
              </button>
              <button
                onClick={() => setDirectionFilter('out')}
                className={`px-2 py-1.5 text-xs rounded-r transition-colors flex items-center gap-1 ${
                  directionFilter === 'out'
                    ? 'bg-green-500 text-white'
                    : 'text-bambu-gray hover:text-white'
                }`}
              >
                <ArrowUp className="w-3 h-3" />
                {t('mqttDebug.outgoing')}
              </button>
            </div>
          </div>
        </div>

        {/* Log Content */}
        <div
          ref={logContainerRef}
          className="flex-1 overflow-auto p-4 font-mono text-xs bg-black min-h-[400px]"
        >
          {logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-bambu-gray">
              <p className="mb-2">{t('mqttDebug.noMessages')}</p>
              {!loggingEnabled && (
                <p className="text-sm">{t('mqttDebug.startLoggingHint')}</p>
              )}
            </div>
          ) : filteredLogs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-bambu-gray">
              <p className="mb-2">{t('mqttDebug.noMessagesMatch')}</p>
              <p className="text-sm">{t('mqttDebug.adjustFilterHint')}</p>
            </div>
          ) : (
            <div className="space-y-1">
              {filteredLogs.map((log: MQTTLogEntry, index: number) => {
                const isExpanded = expandedLogs.has(index);
                const isIncoming = log.direction === 'in';

                return (
                  <div
                    key={index}
                    className={`p-2 rounded cursor-pointer hover:bg-bambu-dark-secondary transition-colors ${
                      isExpanded ? 'bg-bambu-dark-secondary' : ''
                    }`}
                    onClick={() => toggleExpand(index)}
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-bambu-gray shrink-0">
                        {formatTimestamp(log.timestamp)}
                      </span>
                      <span
                        className={`shrink-0 ${
                          isIncoming ? 'text-blue-400' : 'text-green-400'
                        }`}
                        title={isIncoming ? t('mqttDebug.incoming') : t('mqttDebug.outgoing')}
                      >
                        {isIncoming ? (
                          <ArrowDown className="w-3 h-3" />
                        ) : (
                          <ArrowUp className="w-3 h-3" />
                        )}
                      </span>
                      <span className="text-purple-400 shrink-0">{log.topic}</span>
                    </div>
                    {isExpanded ? (
                      <pre className="mt-2 p-3 bg-gray-900 border border-gray-700 rounded text-green-400 overflow-x-auto whitespace-pre-wrap break-all max-h-96 overflow-y-auto text-xs">
                        {formatPayload(log.payload, true)}
                      </pre>
                    ) : (
                      <pre className="mt-1 text-white/80 truncate">
                        {formatPayload(log.payload, false)}
                      </pre>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-4 border-t border-bambu-dark-tertiary">
          <div className="text-sm text-bambu-gray">
            {loggingEnabled ? (
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                {t('mqttDebug.loggingActive')}
              </span>
            ) : (
              <span>{t('mqttDebug.loggingStopped')}</span>
            )}
          </div>
          <Button variant="secondary" onClick={onClose}>
            {t('common.close')}
          </Button>
        </div>
      </div>
    </div>
  );
}
