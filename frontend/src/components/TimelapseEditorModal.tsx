import { useState, useRef, useEffect, useCallback } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  X,
  Save,
  Film,
  Play,
  Pause,
  Scissors,
  Gauge,
  Music,
  Upload,
  Trash2,
  Volume2,
  VolumeX,
  Loader2,
} from 'lucide-react';
import { Button } from './Button';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';

interface TimelapseEditorModalProps {
  archiveId: number;
  timelapseSrc: string;
  onClose: () => void;
  onSave?: () => void;
}

const SPEED_OPTIONS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function TimelapseEditorModal({
  archiveId,
  timelapseSrc,
  onClose,
  onSave,
}: TimelapseEditorModalProps) {
  const { showToast } = useToast();
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  // Video state
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  // Editor state
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioVolume, setAudioVolume] = useState(0.8);
  const [audioMuted, setAudioMuted] = useState(false);


  // Fetch video info
  const { data: videoInfo, isLoading: isLoadingInfo } = useQuery({
    queryKey: ['timelapse-info', archiveId],
    queryFn: () => api.getTimelapseInfo(archiveId),
  });

  // Fetch thumbnails
  const { data: thumbnailData } = useQuery({
    queryKey: ['timelapse-thumbnails', archiveId],
    queryFn: () => api.getTimelapseThumbnails(archiveId, 15),
  });

  // Process mutation
  const processMutation = useMutation({
    mutationFn: () =>
      api.processTimelapse(
        archiveId,
        {
          trimStart,
          trimEnd,
          speed,
          saveMode: 'replace',
        },
        audioFile || undefined
      ),
    onSuccess: (data) => {
      showToast(data.message, 'success');
      onSave?.();
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message || 'Processing failed', 'error');
    },
  });

  // Initialize trimEnd when duration is available
  useEffect(() => {
    if (videoInfo?.duration && trimEnd === 0) {
      setTrimEnd(videoInfo.duration);
    }
  }, [videoInfo?.duration, trimEnd]);

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Video event handlers
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const handleTimeUpdate = () => {
      const time = video.currentTime;
      setCurrentTime(time);

      // Loop within trim region
      if (time >= trimEnd) {
        video.currentTime = trimStart;
      }
    };

    const handleDurationChange = () => {
      setDuration(video.duration);
      if (trimEnd === 0) {
        setTrimEnd(video.duration);
      }
    };

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);

    video.addEventListener('timeupdate', handleTimeUpdate);
    video.addEventListener('durationchange', handleDurationChange);
    video.addEventListener('play', handlePlay);
    video.addEventListener('pause', handlePause);

    return () => {
      video.removeEventListener('timeupdate', handleTimeUpdate);
      video.removeEventListener('durationchange', handleDurationChange);
      video.removeEventListener('play', handlePlay);
      video.removeEventListener('pause', handlePause);
    };
  }, [trimStart, trimEnd]);

  // Sync audio with video
  useEffect(() => {
    const audio = audioRef.current;
    const video = videoRef.current;
    if (!audio || !video || !audioUrl) return;

    audio.currentTime = video.currentTime;
    audio.playbackRate = video.playbackRate;

    if (isPlaying && !audioMuted) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  }, [isPlaying, audioUrl, audioMuted]);

  // Update audio volume
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = audioMuted ? 0 : audioVolume;
    }
  }, [audioVolume, audioMuted]);

  // Update playback rate
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.playbackRate = speed;
    }
    if (audioRef.current) {
      audioRef.current.playbackRate = speed;
    }
  }, [speed]);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    if (isPlaying) {
      video.pause();
    } else {
      // Start from trim start if before it
      if (video.currentTime < trimStart) {
        video.currentTime = trimStart;
      }
      video.play();
    }
  }, [isPlaying, trimStart]);

  const handleSeek = (time: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(trimStart, Math.min(trimEnd, time));
  };

  const handleAudioUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Cleanup previous URL
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }

    setAudioFile(file);
    setAudioUrl(URL.createObjectURL(file));
  };

  const removeAudio = () => {
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }
    setAudioFile(null);
    setAudioUrl(null);
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (audioUrl) {
        URL.revokeObjectURL(audioUrl);
      }
    };
  }, [audioUrl]);

  const trimmedDuration = trimEnd - trimStart;
  const outputDuration = trimmedDuration / speed;

  if (isLoadingInfo) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80">
        <div className="flex items-center gap-3 text-white">
          <Loader2 className="w-6 h-6 animate-spin" />
          Loading video info...
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80">
      <div className="relative bg-bambu-dark-secondary rounded-xl max-w-5xl w-full mx-4 max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary shrink-0">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Film className="w-5 h-5 text-bambu-green" />
            Edit Timelapse
          </h3>
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={() => processMutation.mutate()}
              disabled={processMutation.isPending}
            >
              {processMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Processing...
                </>
              ) : (
                <>
                  <Save className="w-4 h-4" />
                  Save
                </>
              )}
            </Button>
            <button
              onClick={onClose}
              className="p-1 hover:bg-bambu-dark-tertiary rounded transition-colors"
            >
              <X className="w-5 h-5 text-bambu-gray" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Video Preview */}
          <div className="relative">
            <video
              ref={videoRef}
              src={timelapseSrc}
              className="w-full rounded-lg bg-black"
              onClick={togglePlay}
              muted={!!audioUrl}
            />

            {/* Play overlay */}
            {!isPlaying && (
              <button
                onClick={togglePlay}
                className="absolute inset-0 flex items-center justify-center bg-black/30 hover:bg-black/40 transition-colors"
              >
                <div className="p-4 bg-bambu-green rounded-full">
                  <Play className="w-8 h-8 text-white" />
                </div>
              </button>
            )}

            {/* Hidden audio element for music overlay preview */}
            {audioUrl && (
              <audio ref={audioRef} src={audioUrl} loop />
            )}
          </div>

          {/* Timeline with Thumbnails */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-bambu-gray">
              <Scissors className="w-4 h-4" />
              <span>Trim</span>
              <span className="ml-auto">
                {formatTime(trimStart)} - {formatTime(trimEnd)} ({formatTime(trimmedDuration)})
              </span>
            </div>

            {/* Thumbnail strip */}
            <div className="relative h-16 bg-bambu-dark rounded-lg overflow-hidden">
              {/* Thumbnails background */}
              <div className="absolute inset-0 flex">
                {thumbnailData?.thumbnails.map((thumb, i) => (
                  <div
                    key={i}
                    className="flex-1 bg-cover bg-center"
                    style={{
                      backgroundImage: `url(data:image/jpeg;base64,${thumb})`,
                    }}
                  />
                ))}
              </div>

              {/* Trim overlay - grayed out areas */}
              <div
                className="absolute inset-y-0 left-0 bg-black/60"
                style={{ width: `${(trimStart / duration) * 100}%` }}
              />
              <div
                className="absolute inset-y-0 right-0 bg-black/60"
                style={{ width: `${((duration - trimEnd) / duration) * 100}%` }}
              />

              {/* Selected region border */}
              <div
                className="absolute inset-y-0 border-2 border-bambu-green"
                style={{
                  left: `${(trimStart / duration) * 100}%`,
                  right: `${((duration - trimEnd) / duration) * 100}%`,
                }}
              />

              {/* Current time indicator */}
              <div
                className="absolute top-0 bottom-0 w-0.5 bg-white shadow-lg"
                style={{ left: `${(currentTime / duration) * 100}%` }}
              />

              {/* Trim handles */}
              <input
                type="range"
                min={0}
                max={duration}
                step={0.1}
                value={trimStart}
                onChange={(e) => {
                  const val = parseFloat(e.target.value);
                  if (val < trimEnd - 1) {
                    setTrimStart(val);
                    if (videoRef.current && videoRef.current.currentTime < val) {
                      videoRef.current.currentTime = val;
                    }
                  }
                }}
                className="absolute inset-0 w-full opacity-0 cursor-ew-resize"
                style={{ clipPath: 'inset(0 50% 0 0)' }}
              />
              <input
                type="range"
                min={0}
                max={duration}
                step={0.1}
                value={trimEnd}
                onChange={(e) => {
                  const val = parseFloat(e.target.value);
                  if (val > trimStart + 1) {
                    setTrimEnd(val);
                  }
                }}
                className="absolute inset-0 w-full opacity-0 cursor-ew-resize"
                style={{ clipPath: 'inset(0 0 0 50%)' }}
              />
            </div>

            {/* Playback scrubber */}
            <input
              type="range"
              min={0}
              max={duration}
              step={0.1}
              value={currentTime}
              onChange={(e) => handleSeek(parseFloat(e.target.value))}
              className="w-full h-1 bg-bambu-dark-tertiary rounded-lg appearance-none cursor-pointer
                [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
                [&::-webkit-slider-thumb]:bg-bambu-green [&::-webkit-slider-thumb]:rounded-full
                [&::-webkit-slider-thumb]:cursor-pointer"
            />

            {/* Play controls */}
            <div className="flex items-center justify-center gap-2">
              <button
                onClick={togglePlay}
                className="p-2 bg-bambu-green hover:bg-bambu-green-dark rounded-lg transition-colors"
              >
                {isPlaying ? (
                  <Pause className="w-5 h-5 text-white" />
                ) : (
                  <Play className="w-5 h-5 text-white" />
                )}
              </button>
            </div>
          </div>

          {/* Speed Control */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-bambu-gray">
              <Gauge className="w-4 h-4" />
              <span>Speed</span>
              <span className="ml-auto">{speed}x (output: {formatTime(outputDuration)})</span>
            </div>
            <div className="flex gap-1">
              {SPEED_OPTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => setSpeed(s)}
                  className={`flex-1 px-2 py-2 text-sm rounded transition-colors ${
                    speed === s
                      ? 'bg-bambu-green text-white'
                      : 'bg-bambu-dark text-bambu-gray hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>

          {/* Audio Upload */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-bambu-gray">
              <Music className="w-4 h-4" />
              <span>Music Overlay</span>
            </div>

            {audioFile ? (
              <div className="flex items-center gap-3 p-3 bg-bambu-dark rounded-lg">
                <Music className="w-5 h-5 text-bambu-green" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white truncate">{audioFile.name}</p>
                  <p className="text-xs text-bambu-gray">
                    {(audioFile.size / 1024 / 1024).toFixed(1)} MB
                  </p>
                </div>

                {/* Volume control */}
                <button
                  onClick={() => setAudioMuted(!audioMuted)}
                  className="p-2 hover:bg-bambu-dark-tertiary rounded transition-colors"
                >
                  {audioMuted ? (
                    <VolumeX className="w-4 h-4 text-bambu-gray" />
                  ) : (
                    <Volume2 className="w-4 h-4 text-bambu-green" />
                  )}
                </button>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.1}
                  value={audioVolume}
                  onChange={(e) => setAudioVolume(parseFloat(e.target.value))}
                  className="w-20 h-1 bg-bambu-dark-tertiary rounded-lg appearance-none cursor-pointer
                    [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
                    [&::-webkit-slider-thumb]:bg-bambu-green [&::-webkit-slider-thumb]:rounded-full"
                />

                <button
                  onClick={removeAudio}
                  className="p-2 hover:bg-red-500/20 rounded transition-colors"
                >
                  <Trash2 className="w-4 h-4 text-red-400" />
                </button>
              </div>
            ) : (
              <label className="flex flex-col items-center justify-center gap-2 p-6 border-2 border-dashed border-bambu-dark-tertiary rounded-lg cursor-pointer hover:border-bambu-green/50 transition-colors">
                <Upload className="w-8 h-8 text-bambu-gray" />
                <span className="text-sm text-bambu-gray">
                  Drop audio file or click to upload
                </span>
                <span className="text-xs text-bambu-gray/60">
                  MP3, WAV, M4A, AAC, OGG
                </span>
                <input
                  type="file"
                  accept=".mp3,.wav,.m4a,.aac,.ogg,audio/*"
                  onChange={handleAudioUpload}
                  className="hidden"
                />
              </label>
            )}
          </div>

          {/* Summary */}
          <div className="p-3 bg-bambu-dark rounded-lg text-sm space-y-1">
            <p className="text-bambu-gray">
              <span className="text-white">Original:</span> {formatTime(duration)} @ {videoInfo?.width}x{videoInfo?.height}
            </p>
            <p className="text-bambu-gray">
              <span className="text-white">Output:</span> {formatTime(outputDuration)} @ {speed}x speed
              {audioFile && ` + music overlay`}
            </p>
          </div>
        </div>

        {/* Processing overlay */}
        {processMutation.isPending && (
          <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center gap-4">
            <Loader2 className="w-12 h-12 text-bambu-green animate-spin" />
            <p className="text-white text-lg">Processing timelapse...</p>
            <p className="text-bambu-gray text-sm">This may take a few moments</p>
          </div>
        )}
      </div>
    </div>
  );
}
