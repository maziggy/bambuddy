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
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-sm border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">
            Mount External Folder
          </h2>
        </div>

        <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="p-4 space-y-4">
          {/* Path Input */}
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              Folder Path
            </label>
            <input
              type="text"
              value={path}
              onChange={handlePathChange}
              placeholder="/mnt/external/models"
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
            />
            <p className="mt-1 text-xs text-bambu-gray">
              Absolute path to the directory (container path)
            </p>

            {/* Validation Status */}
            {isValidating && (
              <div className="mt-2 flex items-center gap-2 text-sm text-bambu-gray">
                <Loader2 className="w-4 h-4 animate-spin" />
                Validating...
              </div>
            )}

            {validation && !validation.valid && (
              <div className="mt-2 flex items-start gap-2 text-sm text-red-400">
                <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>{validation.error}</span>
              </div>
            )}

            {validation && validation.valid && (
              <div className="mt-2 flex items-start gap-2 text-sm text-green-400">
                <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <div>
                  <p>Valid directory</p>
                  {validation.file_count !== undefined && (
                    <p className="text-xs text-bambu-gray">
                      {validation.file_count} files ({validation.directory_size_mb}MB)
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Name Input */}
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              Folder Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My External Files"
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>

          {/* Options */}
          <div className="space-y-2">
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={readonly}
                onChange={(e) => setReadonly(e.target.checked)}
                className="w-4 h-4 rounded border-bambu-dark-tertiary text-bambu-green focus:ring-bambu-green/50"
              />
              <span className="text-sm text-white">
                Read-only (prevent uploads/deletes)
              </span>
            </label>
          </div>

          {/* Error Message */}
          {createMutation.isError && (
            <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg flex gap-2">
              <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-400">
                {createMutation.error instanceof Error
                  ? createMutation.error.message
                  : "Failed to create folder"}
              </p>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={createMutation.isPending}
              className="px-4 py-2 text-sm font-medium text-white bg-slate-700 hover:bg-slate-600 rounded transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => createMutation.mutate()}
              disabled={!canSubmit}
              className="px-4 py-2 text-sm font-medium text-white bg-bambu-green hover:bg-bambu-green/90 rounded transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {createMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              Create
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
