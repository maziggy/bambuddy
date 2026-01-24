# External Folder Mounting

Bambuddy supports mounting external directories (NAS shares, USB drives, network storage) directly into the File Manager. This allows you to organize your print files across multiple storage locations without uploading everything to Bambuddy.

## Overview

External folders work seamlessly with Bambuddy's File Manager:
- **Real-time discovery**: Files are automatically discovered and indexed
- **Smart refresh**: Uses directory modification tracking to detect changes efficiently
- **Security**: Read-only mode protects your data from accidental modifications
- **Organized**: Folder hierarchy is preserved, no file type filtering

## Features

### 📂 Mount External Directories

Mount any accessible directory (local, NAS, USB, network share) to Bambuddy's File Manager:
1. Open **File Manager** → click **"Mount External"** button
2. Enter the full path to your directory (e.g., `/mnt/nas-storage`)
3. Give it a friendly name
4. Choose **Read-Only** mode (default) or **Read-Write** for full access
5. Click **Mount**

Bambuddy validates the path against:
- Path must be absolute (e.g., `/path/to/folder`, not `~/folder`)
- Path must exist and be a directory
- Path must be readable
- Path must be within configured allowed base paths (see Settings)
- System directories are protected (`/etc`, `/var`, `/usr`, `/bin`, etc.)

### 🔒 Read-Only vs Read-Write Mode

#### Read-Only (Default, Recommended)
- View and print files from the external folder
- Cannot create, delete, or rename files
- Protects against accidental data loss
- Good for NAS shares you want to browse but not modify

#### Read-Write
- Full access: create, rename, delete files
- Useful for USB drives or network shares you manage directly
- Be careful — deletions are permanent

### 🔄 Smart File Discovery

External folders use smart change detection:
- **First scan**: Recursively indexes all files in the directory
- **Subsequent scans**: Checks directory modification time (mtime)
- **Change detection**: If mtime hasn't changed, skips expensive re-scan
- **Force rescan**: Use the rescan button to force a full scan anytime
- **Configurable depth**: Set maximum scan depth in Settings (1-20 levels)

The approach is efficient even for large directories (thousands of files).

### 📊 File Information

For each file in an external folder, Bambuddy stores:
- Filename and relative path (preserves folder structure)
- File size
- Last modified time
- File type (3MF, STL, GCODE, etc.)
- External flag (for UI indicators)

### 🎯 Filtering

External folders automatically filter to show only print-relevant file types:
- `.3mf` - Bambu Lab project files
- `.stl` - 3D mesh files
- `.gcode` - CNC/printer control files

This prevents cluttering the UI with unrelated files (documents, images, etc.).

## Configuration

External folder mounting behavior is configured in **Settings → External Folders**:

### Enable/Disable
Toggle external folder mounting on/off globally. When disabled, mounted folders remain in the database but are not scanned.

### Allowed Base Paths
Security feature: specify which container paths can be mounted.

Example:
```
/mnt/external,/mnt/nas,/mnt/usb,/home/prints
```

Only paths within these base directories can be mounted. This prevents accidentally exposing sensitive system directories.

**Default**: `/mnt/external`

### Maximum Scan Depth
Controls how many directory levels to recursively scan.

- `1` = Only files in root folder (fastest)
- `5` = Scan 5 levels deep (recommended for most cases)
- `10` = Scan 10 levels deep (handles complex nested structures)
- `20` = Scan 20 levels deep (maximum, very slow for large trees)

**Default**: `5`

### Security Note (Docker Users)
In Docker, paths refer to **container paths**, not your host filesystem:
- Mount your NAS to a container path: `-v /mnt/nas:/mnt/nas`
- Use the container path in Bambuddy: `/mnt/nas`
- Don't use host paths: `/home/user/nas` (incorrect)

## Use Cases

### NAS Storage
Mount your NAS storage and browse files without uploading:
```bash
# In Docker: -v /path/to/nas:/mnt/nas
# In Bambuddy: Mount /mnt/nas
```

### USB Drive
Plug in a USB drive and mount it:
```bash
# Linux: sudo mount /dev/sda1 /mnt/usb
# In Bambuddy: Mount /mnt/usb (Read-Only recommended)
```

### Print Farm File Sharing
Organize prints across multiple external storage pools:
- Main workstation: `/mnt/storage1`
- Backup drive: `/mnt/storage2`
- NAS archive: `/mnt/nas-archive`

Mount all three and organize by project.

### Slicer Output Staging
Point Bambuddy to your slicer's output directory:
```bash
# Bambu Studio output
# Windows: Mount C:\Users\[user]\Documents\Bambu Studio
# Linux: Mount ~/Documents/Bambu\ Studio
```

Files appear immediately after slicing without manual upload.

## Troubleshooting

### "Path is not readable"
The Bambuddy process doesn't have read permissions. Verify:
- Permissions: `ls -ld /path/to/folder` shows `drwxr-xr-x` or similar
- Fix: `chmod 755 /path/to/folder`

### "Path is not within allowed directories"
The path isn't in your **Allowed Base Paths** setting. Add it in Settings → External Folders.

### "Cannot mount system directories"
Paths like `/etc`, `/var`, `/usr`, `/bin` are protected for security. Use a different path.

### Files not appearing
1. Check the **Allowed Base Paths** setting
2. Click the **Rescan** button on the folder
3. Verify files match supported types (`.3mf`, `.stl`, `.gcode`)
4. Check permissions with `ls -la /path/to/folder`

### Too slow
Reduce **Maximum Scan Depth** in Settings (e.g., from 10 to 3). Fewer levels = faster scanning.

## Performance Tips

1. **Use read-only mode** - Skips permission checks for write operations
2. **Reduce scan depth** - Limit recursion depth in Settings
3. **Use mtime-based refresh** - Bambuddy caches mtime to skip unnecessary scans
4. **Keep folder count reasonable** - Don't mount hundreds of folders
5. **Monitor folder size** - Very large directories (10,000+ files) may be slow

## API

External folders are accessible via REST API:

### List External Folders
```bash
GET /api/v1/library/folders?external_only=true
```

### Mount External Folder
```bash
POST /api/v1/library/folders/external
{
  "name": "My NAS",
  "external_path": "/mnt/nas",
  "parent_id": null,
  "external_readonly": true,
  "external_show_hidden": false
}
```

### Rescan External Folder
```bash
POST /api/v1/library/folders/{folder_id}/rescan
{
  "force_rescan": true
}
```

### Get Scan Results
```bash
GET /api/v1/library/folders/{folder_id}/external_stats
```

Returns:
```json
{
  "last_scan": "2024-01-24T12:34:56Z",
  "file_count": 42,
  "scan_duration_ms": 234
}
```

## See Also

- [File Manager Guide](./file-manager.md)
- [Settings Documentation](./settings.md)
- [API Reference](../wiki.bambuddy.cool/reference/api/)
