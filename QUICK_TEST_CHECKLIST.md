# Quick Testing Checklist - External Folder Mounting

**When you run the application**, use this checklist to verify the feature works.

---

## 🚀 Getting Started

1. **Start Bambuddy**
   ```bash
   docker compose up -d --build
   ```

2. **Access Web Interface**
   ```
   http://localhost:8000
   ```

3. **Check Application Started**
   - [ ] Page loads without errors
   - [ ] Settings accessible
   - [ ] File Manager page accessible

---

## ✅ Test Checklist

### 1. Settings > File Manager Tab
- [ ] "File Manager" tab exists in Settings
- [ ] External Folder Settings panel visible
- [ ] "Enable External Folders" toggle present
- [ ] "Allowed Paths" input visible
- [ ] "Max Scan Depth" slider visible
- [ ] All settings have labels and descriptions

### 2. File Manager > External Folder Button
- [ ] File Manager page has "External Folder" button
- [ ] Button has appropriate styling
- [ ] Button is clickable

### 3. External Folder Modal
- [ ] Clicking "External Folder" opens a modal
- [ ] Modal has:
  - [ ] Path input field
  - [ ] Name input field
  - [ ] Readonly toggle
  - [ ] Show Hidden toggle
  - [ ] Extensions input
  - [ ] Validate button or auto-validation

### 4. Path Validation
- [ ] Type path: `/mnt/external`
- [ ] Wait 500ms for validation
- [ ] Should show:
  - [ ] "Valid directory" message
  - [ ] File count: "4 files"
  - [ ] Directory size: "0.0MB"
- [ ] Create button becomes enabled

### 5. Create External Folder
- [ ] Auto-fill Name field with "external"
- [ ] Keep readonly = true (default)
- [ ] Click "Create"
- [ ] Modal closes
- [ ] New folder appears in File Manager tree

### 6. Visual Indicators
- [ ] External folder has link icon (🔗)
- [ ] External folder has lock icon (🔒) for readonly
- [ ] Folder shows file count: "4"

### 7. Browse External Files
- [ ] Click the external folder
- [ ] Files display:
  - [ ] test.3mf
  - [ ] test.stl
  - [ ] test.gcode
  - [ ] subdir/nested.gcode
- [ ] Files have "EXT" badge

### 8. Download Works
- [ ] Click download on one file
- [ ] File downloads successfully
- [ ] Can open downloaded file

### 9. Readonly Protection
- [ ] Upload button is DISABLED
- [ ] Delete button is DISABLED
- [ ] Files cannot be modified

### 10. Rescan Folder
- [ ] Rescan button appears
- [ ] Click Rescan
- [ ] Shows loading spinner
- [ ] Completes with message: "Scan complete: +0, ~0, -0"
- [ ] Shows scan duration
- [ ] Shows last scanned timestamp

### 11. Test Change Detection
- [ ] On host, add file: `echo "new" > /tmp/test-external/new.3mf`
- [ ] In app, click Rescan
- [ ] Toast shows: "Scan complete: +1, ~0, -0"
- [ ] new.3mf appears in list

### 12. Test Deletion Detection
- [ ] On host, delete file: `rm /tmp/test-external/test.stl`
- [ ] In app, click Rescan
- [ ] Toast shows: "Scan complete: +0, ~0, -1"
- [ ] test.stl disappears from list

### 13. Extension Filtering
- [ ] Create another external folder with same path
- [ ] Set extensions: ".3mf,.gcode" (exclude .stl)
- [ ] Create folder
- [ ] Only .3mf and .gcode files shown (3 files, not 4)
- [ ] test.stl is hidden

### 14. Settings Management
- [ ] Settings > File Manager tab
- [ ] Change max_scan_depth to 1
- [ ] Create new external folder
- [ ] Rescan with depth=1
- [ ] Nested files may not appear
- [ ] Change back to 10
- [ ] Rescan shows nested files again

### 15. Multiple Folders
- [ ] Create second test directory: `mkdir /tmp/external2`
- [ ] Add files: `echo "test" > /tmp/external2/test2.3mf`
- [ ] Mount via External Folder button
- [ ] Both folders appear in tree
- [ ] Can switch between them
- [ ] File lists update correctly

### 16. Security: Invalid Paths
Try to mount these paths (should all fail):

- [ ] `/etc` → Error: "Cannot mount system directories"
- [ ] `/sys` → Error: "Cannot mount system directories"
- [ ] `/proc` → Error: "Cannot mount system directories"
- [ ] `/root` → Error: "Cannot mount system directories"
- [ ] `/invalid/path` → Error: "Path does not exist"
- [ ] `/home/user` (outside allowed) → Error: "Path is not within allowed directories"

---

## 📊 Results Template

```
Date: _____________
Tester: _____________
Bambuddy Version: _____________

TESTS PASSED: ___ / 16
TESTS FAILED: ___ / 16

Passed Tests:
- [ ] Settings > File Manager Tab
- [ ] External Folder Button
- [ ] External Folder Modal
- [ ] Path Validation
- [ ] Create Folder
- [ ] Visual Indicators
- [ ] Browse Files
- [ ] Download
- [ ] Readonly Protection
- [ ] Rescan
- [ ] Change Detection
- [ ] Deletion Detection
- [ ] Extension Filtering
- [ ] Settings Management
- [ ] Multiple Folders
- [ ] Security Validation

Issues Found:
1. _________________________________
2. _________________________________
3. _________________________________

Notes:
_________________________________
_________________________________
```

---

## 🐛 Troubleshooting

If tests don't work, check:

1. **Test directory exists**
   ```bash
   ls -la /tmp/test-external/
   ```

2. **Docker mount working**
   ```bash
   docker exec bambuddy ls -la /mnt/external
   ```

3. **Check logs**
   ```bash
   docker compose logs -f | grep -i external
   ```

4. **Check database**
   ```bash
   sqlite3 app/data/bambuddy.db
   SELECT id, name, is_external FROM library_folders WHERE is_external = 1;
   ```

5. **Check API response**
   ```bash
   curl -X POST http://localhost:8000/api/v1/library/folders/external/validate \
     -H "Content-Type: application/json" \
     -d '{"path": "/mnt/external"}'
   ```

---

## ✅ Sign-Off

- [ ] All 16 tests passed
- [ ] No blocking issues found
- [ ] Feature ready for production
- [ ] Tester: _______________
- [ ] Date: _______________
