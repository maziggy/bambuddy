import { useState, useCallback } from "react"
import { useMutation } from "@tanstack/react-query"
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react"
import { api } from "../api/client"
import { debounce } from "../utils/debounce"

interface ExternalFolderModalProps {
  parentId: number | null
  onClose: () => void
  onSuccess: () => void
}

export function ExternalFolderModal({ parentId, onClose, onSuccess }: ExternalFolderModalProps) {
  const [path, setPath] = useState("")
  const [name, setName] = useState("")
  const [readonly, setReadonly] = useState(true)
  const [extensions, setExtensions] = useState(".3mf,.stl,.gcode")
  const [isValidating, setIsValidating] = useState(false)
  const [validation, setValidation] = useState<{
    valid: boolean
    error?: string
    file_count?: number
    directory_size_mb?: number
  } | null>(null)

  // Debounced validation
  const validatePath = useCallback(
    debounce(async (p: string) => {
      if (!p.trim()) {
        setValidation(null)
        return
      }

      setIsValidating(true)
      try {
        const result = await api.validateExternalPath({ path: p })
        setValidation(result)
        if (result.valid && !name) {
          // Auto-fill name from last path segment
          const pathSegments = p.split("/")
          setName(pathSegments[pathSegments.length - 1] || p)
        }
      } catch (error) {
        setValidation({
          valid: false,
          error: error instanceof Error ? error.message : "Validation failed",
        })
      } finally {
        setIsValidating(false)
      }
    }, 500),
    [name]
  )

  const handlePathChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newPath = e.target.value
    setPath(newPath)
    validatePath(newPath)
  }

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!validation?.valid || !path.trim() || !name.trim()) {
        throw new Error("Invalid form data")
      }

      return api.createExternalFolder({
        name: name.trim(),
        external_path: path.trim(),
        parent_id: parentId,
        external_readonly: readonly,
        external_show_hidden: false,  // Hidden files are never shown
        external_extensions: extensions || null,
      })
    },
    onSuccess: () => {
      onSuccess()
      onClose()
    },
  })

  const canSubmit =
    validation?.valid &&
    path.trim().length > 0 &&
    name.trim().length > 0 &&
    !createMutation.isPending

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-slate-800 rounded-lg shadow-xl max-w-md w-full mx-4">
        <div className="px-6 py-4 border-b border-gray-200 dark:border-slate-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Mount External Folder
          </h2>
        </div>

        <div className="px-6 py-4 space-y-4">
          {/* Path Input */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Folder Path
            </label>
            <input
              type="text"
              value={path}
              onChange={handlePathChange}
              placeholder="/mnt/external/models"
              className="w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg
                         dark:bg-slate-700 dark:text-white text-sm focus:outline-none focus:ring-2
                         focus:ring-bambu-green/50"
            />
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Absolute path to the directory (container path)
            </p>

            {/* Validation Status */}
            {isValidating && (
              <div className="mt-2 flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin" />
                Validating...
              </div>
            )}

            {validation && !validation.valid && (
              <div className="mt-2 flex items-start gap-2 text-sm text-red-600 dark:text-red-400">
                <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>{validation.error}</span>
              </div>
            )}

            {validation && validation.valid && (
              <div className="mt-2 flex items-start gap-2 text-sm text-green-600 dark:text-green-400">
                <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <div>
                  <p>Valid directory</p>
                  {validation.file_count !== undefined && (
                    <p className="text-xs text-gray-600 dark:text-gray-400">
                      {validation.file_count} files ({validation.directory_size_mb}MB)
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Name Input */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Folder Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My External Files"
              className="w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg
                         dark:bg-slate-700 dark:text-white text-sm focus:outline-none focus:ring-2
                         focus:ring-bambu-green/50"
            />
          </div>

          {/* Extensions Filter */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              File Extensions (optional)
            </label>
            <input
              type="text"
              value={extensions}
              onChange={(e) => setExtensions(e.target.value)}
              placeholder=".3mf,.stl,.gcode"
              className="w-full px-3 py-2 border border-gray-300 dark:border-slate-600 rounded-lg
                         dark:bg-slate-700 dark:text-white text-sm focus:outline-none focus:ring-2
                         focus:ring-bambu-green/50"
            />
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Comma-separated list (e.g., .3mf,.stl). Leave empty to include all files.
            </p>
          </div>

          {/* Options */}
          <div className="space-y-2">
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={readonly}
                onChange={(e) => setReadonly(e.target.checked)}
                className="w-4 h-4 rounded border-gray-300 text-bambu-green
                           focus:ring-bambu-green/50"
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">
                Read-only (prevent uploads/deletes)
              </span>
            </label>
          </div>

          {/* Error Message */}
          {createMutation.isError && (
            <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800
                           rounded-lg flex gap-2">
              <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-600 dark:text-red-400">
                {createMutation.error instanceof Error
                  ? createMutation.error.message
                  : "Failed to create folder"}
              </p>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="px-6 py-4 border-t border-gray-200 dark:border-slate-700 flex gap-3 justify-end">
          <button
            onClick={onClose}
            disabled={createMutation.isPending}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100
                      dark:bg-slate-700 hover:bg-gray-200 dark:hover:bg-slate-600 rounded-lg
                      transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!canSubmit}
            className="px-4 py-2 text-sm font-medium text-white bg-bambu-green hover:bg-bambu-green/90
                      rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {createMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            Create
          </button>
        </div>
      </div>
    </div>
  )
}
