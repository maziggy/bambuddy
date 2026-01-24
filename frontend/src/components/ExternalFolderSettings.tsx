import { useQuery, useMutation } from "@tanstack/react-query"
import { AlertCircle, CheckCircle2 } from "lucide-react"
import { api } from "../api/client"

export function ExternalFolderSettings() {
  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  })

  const updateMutation = useMutation({
    mutationFn: api.updateSettings,
    onSuccess: () => {
      // Settings will auto-refresh via React Query
    },
  })

  const handleToggle = (key: string, value: boolean) => {
    updateMutation.mutate({ [key]: value } as any)
  }

  const handleChange = (key: string, value: string | number) => {
    updateMutation.mutate({ [key]: value } as any)
  }

  if (isLoading || !settings) {
    return <div className="text-gray-500">Loading settings...</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
          External Folder Settings
        </h3>
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
          Configure how Bambuddy mounts and scans external directories (NAS, USB drives, network shares, etc.)
        </p>
      </div>

      {/* Enable/Disable */}
      <div className="border border-gray-200 dark:border-slate-700 rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h4 className="text-sm font-medium text-gray-900 dark:text-white">
              Enable External Folders
            </h4>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
              Allow mounting external directories in the File Manager
            </p>
          </div>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={settings.external_library_enabled}
              onChange={(e) =>
                handleToggle("external_library_enabled", e.target.checked)
              }
              className="w-4 h-4 rounded border-gray-300 text-bambu-green
                         focus:ring-bambu-green/50"
            />
          </label>
        </div>
      </div>

      {/* Allowed Paths */}
      <div className="border border-gray-200 dark:border-slate-700 rounded-lg p-4">
        <label className="block text-sm font-medium text-gray-900 dark:text-white mb-2">
          Allowed Base Paths
        </label>
        <p className="text-xs text-gray-600 dark:text-gray-400 mb-3">
          Comma-separated list of container paths that can be mounted. Example: <code>/mnt/nas,/mnt/external</code>
        </p>
        <textarea
          value={settings.external_library_allowed_paths}
          onChange={(e) =>
            handleChange("external_library_allowed_paths", e.target.value)
          }
          rows={3}
          className="w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg
                     dark:bg-slate-700 dark:text-white text-sm focus:outline-none focus:ring-2
                     focus:ring-bambu-green/50 font-mono text-xs"
          placeholder="/mnt/external,/mnt/nas,/mnt/models"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
          <strong>Security Note:</strong> Only paths listed here can be mounted. Use container paths (not host paths).
        </p>
      </div>

      {/* Max Scan Depth */}
      <div className="border border-gray-200 dark:border-slate-700 rounded-lg p-4">
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-900 dark:text-white">
            Maximum Scan Depth
          </label>
          <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
            Maximum number of directory levels to scan recursively (1-20)
          </p>
        </div>
        <div className="flex items-center gap-4">
          <input
            type="range"
            min="1"
            max="20"
            value={settings.external_library_max_scan_depth}
            onChange={(e) =>
              handleChange("external_library_max_scan_depth", parseInt(e.target.value))
            }
            className="flex-1"
          />
          <span className="w-12 text-center text-sm font-medium text-gray-900 dark:text-white">
            {settings.external_library_max_scan_depth}
          </span>
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
          Deeper scans allow accessing files in nested directories but may be slower.
        </p>
      </div>

      {/* Cache Thumbnails */}
      <div className="border border-gray-200 dark:border-slate-700 rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h4 className="text-sm font-medium text-gray-900 dark:text-white">
              Cache Thumbnails
            </h4>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
              Store thumbnails for external files in internal storage for faster loading
            </p>
          </div>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={settings.external_library_cache_thumbnails}
              onChange={(e) =>
                handleToggle("external_library_cache_thumbnails", e.target.checked)
              }
              className="w-4 h-4 rounded border-gray-300 text-bambu-green
                         focus:ring-bambu-green/50"
            />
          </label>
        </div>
      </div>

      {/* Help */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <h4 className="text-sm font-medium text-blue-900 dark:text-blue-100 mb-2 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" />
          Docker Volume Mount Example
        </h4>
        <p className="text-xs text-blue-800 dark:text-blue-200 mb-3">
          Add to your docker-compose.yml:
        </p>
        <pre className="bg-blue-100 dark:bg-slate-800 p-3 rounded text-xs overflow-auto font-mono
                        text-blue-900 dark:text-blue-100">
{`volumes:
  - /path/on/host:/mnt/external:ro
  - /nas/models:/mnt/nas:ro`}
        </pre>
      </div>

      {/* Status */}
      {updateMutation.isPending && (
        <div className="text-sm text-gray-600 dark:text-gray-400">
          Updating settings...
        </div>
      )}

      {updateMutation.isSuccess && (
        <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
          <CheckCircle2 className="w-4 h-4" />
          Settings updated
        </div>
      )}

      {updateMutation.isError && (
        <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400">
          <AlertCircle className="w-4 h-4" />
          Failed to update settings
        </div>
      )}
    </div>
  )
}
