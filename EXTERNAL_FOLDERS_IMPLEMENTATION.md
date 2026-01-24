# External Directory Mounting Implementation for Bambuddy

**Status**: Core backend and frontend components implemented
**Date Started**: January 23, 2026
**GitHub Issue**: #124 - External Directory Mounting in File Manager

## Summary

This document describes the implementation of the external directory mounting feature for Bambuddy's File Manager. Users can now mount external directories (NAS shares, USB drives, network storage) as top-level folders without duplicating files.

## What's Implemented

### Phase 1: Database Schema ✅
- Added external folder support fields to `LibraryFolder` model:
  - `is_external`: Boolean flag
  - `external_path`: Container path to external directory
  - `external_readonly`: Toggle for write protection
  - `external_show_hidden`: Show/hide dotfiles
  - `external_extensions`: Comma-separated file extensions filter
  - `external_last_scan`: Timestamp of last scan
  - `external_dir_mtime`: Directory modification time for smart refresh

- Added external file support fields to `LibraryFile` model:
  - `is_external`: Boolean flag
  - `external_mtime`: File modification time for change detection

- Database migrations added to `run_migrations()` in `database.py`

### Phase 2: Settings Schema ✅
- Added to `AppSettings` schema:
  - `external_library_enabled`: Enable/disable feature
  - `external_library_allowed_paths`: Comma-separated allowlist of base paths
  - `external_library_max_scan_depth`: Max recursion depth (1-20)
  - `external_library_cache_thumbnails`: Cache external file thumbnails

- Updated settings route parsing for boolean and integer fields

### Phase 3: Backend Service Layer ✅
- Created `ExternalLibraryService` (`backend/app/services/external_library.py`):
  - `validate_external_path()`: Multi-layer security validation
    - Checks if path is absolute
    - Verifies path exists and is a directory
    - Validates against security allowlist
    - Blocks system directories
    - Checks read permissions
    - Resolves and validates symlinks

  - `scan_external_folder()`: Smart refresh with caching
    - Checks directory mtime before rescanning
    - Recursively enumerates files
    - Respects max_depth, hidden files, extensions filters
    - Batch database commits (every 100 files)
    - Updates folder metadata

  - `enumerate_files_recursive()`: Recursive file enumeration
    - Filters by extensions
    - Respects max_depth limit
    - Skips hidden files if disabled

  - Helper functions:
    - `is_safe_path()`: Security validation
    - `is_system_directory()`: Blocks sensitive paths
    - `parse_extensions_filter()`: Parses extension lists
    - `get_allowed_paths()`: Retrieves settings

### Phase 4: Pydantic Schemas ✅
- `ExternalFolderCreate`: For creating new mounts
- `ExternalFolderUpdate`: For updating settings
- `ExternalFolderValidateRequest/Response`: For path validation
- `ExternalFolderScanResponse`: For scan results
- Updated `FolderResponse` and `FileResponse` to include external fields

### Phase 5: API Endpoints ✅
- `POST /library/folders/external/validate`: Validate path with preview
- `POST /library/folders/external`: Create external folder mount
- `POST /library/folders/{folder_id}/scan`: Scan and sync files (with smart refresh)
- `PUT /library/folders/{folder_id}/external`: Update folder settings
- Updated existing endpoints to handle external folders:
  - `GET /library/folders`: Returns external folder data with accessibility status
  - `GET /library/folders/{folder_id}`: Includes external fields
  - `PUT /library/folders/{folder_id}`: Includes external fields

### Phase 6: Frontend Components ✅
- **ExternalFolderModal.tsx**: Modal for creating external folder mounts
  - Path input with debounced real-time validation
  - File count and directory size preview
  - Name input, readonly toggle, show hidden toggle, extension filter
  - Loading and error states
  - Form validation before submission

- **ExternalFolderSettings.tsx**: Settings panel
  - Toggle to enable/disable feature
  - Textarea for allowed paths (comma-separated)
  - Max scan depth slider (1-20)
  - Cache thumbnails toggle
  - Docker volume mount example
  - Auto-save with React Query

### Phase 7: API Client Updates ✅
- Added TypeScript types for external folders
- Updated `LibraryFolder` and `LibraryFolderTree` interfaces
- Updated `LibraryFile` and `LibraryFileListItem` interfaces
- Added API methods:
  - `validateExternalPath()`
  - `createExternalFolder()`
  - `scanExternalFolder()`
  - `updateExternalFolder()`

## Key Design Decisions

### Security
- **Allowlist-based access control**: Only paths in `external_library_allowed_paths` can be mounted
- **System directory blocking**: Cannot mount `/etc`, `/sys`, `/proc`, etc.
- **Symlink resolution**: Validates symlinks don't escape allowed paths
- **Read-only mode**: Prevents accidental or malicious writes to external storage
- **Logging**: All security violations logged for audit

### Performance
- **Smart refresh**: Only rescans if directory mtime changed
- **Batch commits**: Database operations batched every 100 files
- **Async I/O**: All filesystem operations use `asyncio.to_thread()`
- **Extension filtering**: Pre-scans can filter files early

### UX
- **Validation preview**: Shows file count before creating mount
- **Visual indicators**: External folders marked with link icon, lock icon if readonly
- **Auto-naming**: Suggests name from last path segment
- **Rescan button**: Users can manually trigger sync
- **Status messages**: Clear feedback on operations

## Files Modified/Created

### Backend Files
**Created**:
- `backend/app/services/external_library.py` (350+ lines)

**Modified**:
- `backend/app/models/library.py` - Added external fields
- `backend/app/core/database.py` - Added migrations
- `backend/app/schemas/settings.py` - Added settings
- `backend/app/schemas/library.py` - Added schemas
- `backend/app/api/routes/settings.py` - Updated parsing
- `backend/app/api/routes/library.py` - Added endpoints, updated existing

### Frontend Files
**Created**:
- `frontend/src/components/ExternalFolderModal.tsx` (300+ lines)
- `frontend/src/components/ExternalFolderSettings.tsx` (250+ lines)
- `frontend/src/utils/debounce.ts` (20 lines)

**Modified**:
- `frontend/src/api/client.ts` - Added types and API methods

## Complete Frontend Implementation ✅

All UI components have been fully integrated:

### FileManagerPage Updates
- Added "External Folder" button with link icon
- Visual indicators for external folders:
  - Link icon (blue) instead of folder icon for external mounts
  - Lock icon (🔒) for readonly folders
  - Alert icon (⚠️) for inaccessible paths
- Rescan button with status display showing:
  - Last scan timestamp
  - Real-time scan stats (added/updated/removed counts)
  - Animated loading indicator
- Upload and Delete buttons automatically disabled for readonly external folders
- Full modal integration with folder refresh on success

### SettingsPage Updates
- Added "File Manager" tab to settings navigation
- ExternalFolderSettings component fully integrated
- Settings auto-save via React Query mutations
- Docker volume mount examples with explanatory text
- Configuration UI for all external folder settings

### Remaining Tasks (Not Critical for MVP)

1. **Testing** (Phases 10-11):
   - Unit tests for ExternalLibraryService (path validation, scanning, security)
   - Integration tests for API endpoints
   - Frontend component tests
   - Full end-to-end testing with real external directories

2. **Documentation** (Phase 12):
   - Docker compose volume mount examples in docker-compose.yml
   - Setup guide for NAS/network shares
   - CONTRIBUTING guide for extending the feature

3. **Polish** (Optional):
   - File watcher for real-time sync (instead of manual rescan)
   - Parallel directory scanning for large directories
   - Thumbnail pre-generation for external files
   - Bandwidth throttling for large scans

## How to Use (When Integrated)

### As a User

1. **Enable the feature**:
   - Settings > File Manager > Enable External Folders
   - Set allowed paths: `/mnt/nas,/mnt/external`

2. **Mount a directory**:
   - File Manager > External Folder button
   - Enter path: `/mnt/nas/3d-models`
   - Configure settings:
     - Name: "NAS Models"
     - Read-only: Yes
     - Show hidden: No
     - Extensions: ".3mf,.stl"
   - Click Create

3. **Browse files**:
   - Files appear in folder tree with link icon
   - Click to view, download, or preview
   - Rescan button updates with changes

### As a Developer

**Validating a path**:
```python
service = ExternalLibraryService(db)
is_valid, error = await service.validate_external_path("/mnt/nas")
if is_valid:
    # Proceed with creating folder
```

**Scanning a folder**:
```python
result = await service.scan_external_folder(folder_id, force_rescan=False)
print(f"Added: {result['added']}, Updated: {result['updated']}, Removed: {result['removed']}")
```

**Checking if file is accessible**:
```python
file_path = await service.get_external_file_path(file_id)
if file_path:
    # Safe to access file
```

## API Response Examples

### Validate Path
```json
{
  "valid": true,
  "accessible": true,
  "file_count": 142,
  "directory_size_mb": 856.5
}
```

### Scan Folder
```json
{
  "added": 10,
  "updated": 2,
  "removed": 1,
  "scan_duration_seconds": 3.45,
  "last_scan": "2026-01-23T15:30:45.123Z"
}
```

### Folder Response
```json
{
  "id": 5,
  "name": "NAS Models",
  "is_external": true,
  "external_path": "/mnt/nas/3d-models",
  "external_readonly": true,
  "external_show_hidden": false,
  "external_extensions": ".3mf,.stl",
  "external_last_scan": "2026-01-23T15:30:45.123Z",
  "external_accessible": true,
  "file_count": 142,
  ...
}
```

## Testing the Feature

### Manual Testing Checklist
- [ ] Create test directory on host
- [ ] Add test files (3MF, STL, G-code)
- [ ] Mount in docker-compose
- [ ] Create external folder via API
- [ ] Verify files appear in File Manager
- [ ] Test file preview and download
- [ ] Test readonly restriction
- [ ] Test rescan functionality
- [ ] Add/delete files on host
- [ ] Verify changes sync
- [ ] Test symlink validation
- [ ] Test path escape attempts

### Running Tests
```bash
# Backend unit tests
pytest backend/tests/unit/services/test_external_library.py

# Backend integration tests
pytest backend/tests/integration/test_external_library_api.py

# Frontend tests
npm test -- ExternalFolderModal.tsx
```

## Known Limitations

1. **Thumbnail generation**: External file thumbnails not yet generated from 3MF files
2. **Large directories**: Very large directories (10k+ files) may be slow to scan
3. **Network issues**: If NAS becomes unreachable, files still appear in list but can't be accessed
4. **File hash**: External files not hashed (for performance), so deduplication not available
5. **Path changes**: External folder paths cannot be changed once created (must delete and recreate)

## Future Enhancements

- WebDAV support for network shares
- S3/cloud storage mounting
- Automatic thumbnail pre-generation
- File watcher for real-time sync
- Bandwidth throttling for large scans
- Parallel directory scanning
- File metadata caching

## Security Considerations

1. **Always use container paths**, not host paths in docker-compose
2. **Recommend read-only mounts** (`:ro` flag) for untrusted sources
3. **Set reasonable max_scan_depth** to prevent abuse
4. **Use allowlist** to restrict mountable paths
5. **Monitor logs** for suspicious path validation failures

## Contributing

When extending this feature:
1. Follow existing error handling patterns
2. Use `asyncio.to_thread()` for blocking I/O
3. Log security-relevant events
4. Add migrations to `run_migrations()`
5. Test with large directories
6. Validate all user input (paths, extensions, etc.)

## Support

For issues or questions:
- Check `/tmp` cleanup if disk space issues occur
- Review logs: `docker compose logs bambuddy`
- Verify mount point exists on host
- Ensure container has read access to mount path

---

**Implementation Date**: January 23, 2026
**Implemented By**: Claude Code with Haiku 4.5
**Target Release**: v0.1.7
