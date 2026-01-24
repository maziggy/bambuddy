# External Folder Mounting - Testing Guide

**Date**: January 23, 2026
**Feature**: External Directory Mounting (Issue #124)

This guide provides step-by-step instructions for testing the external folder mounting feature.

---

## ✅ Pre-Test Checklist

- [ ] Backend code compiles without errors
- [ ] Frontend components are in place
- [ ] Settings page has File Manager tab
- [ ] Database migrations can run
- [ ] Docker environment is set up
- [ ] Test directory created on host

---

## 🚀 Quick Start Test

### 1. Prepare Test Environment

```bash
# Create test directory with sample files
mkdir -p /tmp/test-external
cd /tmp/test-external

# Add some test files
echo "test" > test.3mf
echo "test" > test.stl
echo "test" > test.gcode
mkdir subdir
echo "test" > subdir/nested.gcode
```

### 2. Update docker-compose.yml

Add volume mount to the Bambuddy service:
```yaml
services:
  bambuddy:
    volumes:
      - /tmp/test-external:/mnt/external:ro  # Read-only mount
```

### 3. Start Bambuddy

```bash
docker compose up -d --build
docker compose logs -f  # Watch for startup messages
```

### 4. Access Web Interface

```
http://localhost:8000
Navigate to: Settings > File Manager
```

---

## 🧪 Test Scenarios

### Scenario 1: Path Validation ✅

**Expected**: Real-time validation with file preview

Steps:
1. Settings > File Manager
2. Verify "Enable External Folders" is ON
3. Verify allowed paths include `/mnt/external`
4. Navigate to File Manager > External Folder button
5. Start typing path: `/mnt/external`

Expected Results:
- [ ] "Validating..." spinner shows
- [ ] After 500ms: "Valid directory" message appears
- [ ] Shows file count: "4 files (0.0MB)"
- [ ] Create button becomes enabled

### Scenario 2: Create External Folder ✅

**Expected**: Folder appears in File Manager with link icon

Steps:
1. Complete path validation (above)
2. Auto-filled Name: "external"
3. Keep defaults (readonly=true, extensions=all)
4. Click "Create"

Expected Results:
- [ ] Modal closes
- [ ] Folder list refreshes
- [ ] New "external" folder appears with blue link icon 🔗
- [ ] Shows lock icon 🔒 (readonly)
- [ ] File count shows "4"
- [ ] Rescan button appears with "Last scanned: ..." timestamp

### Scenario 3: Browse External Files ✅

**Expected**: Files are searchable and downloadable

Steps:
1. Click the external folder
2. Verify files display in grid/list view
3. Try download of test.gcode
4. Try preview of test.3mf

Expected Results:
- [ ] All 4 files visible (including nested.gcode)
- [ ] Files have "EXT" badge
- [ ] Download works
- [ ] Preview shows file content
- [ ] Upload button is DISABLED (readonly)
- [ ] Delete button is DISABLED (readonly)

### Scenario 4: Rescan Folder ✅

**Expected**: Detects changes on external storage

Steps:
1. Files already listed from Scenario 3
2. On host, add new file: `echo "new" > /tmp/test-external/new.3mf`
3. In Bambuddy, click "Rescan" button
4. Watch for loading spinner

Expected Results:
- [ ] Spinner shows while scanning
- [ ] Toast notification appears: "Scan complete: +1, ~0, -0"
- [ ] New "new.3mf" file appears in list
- [ ] "Last scanned" timestamp updates
- [ ] Scan duration shows (e.g., "0.45 seconds")

### Scenario 5: Detect Deletions ✅

**Expected**: Deleted files are removed from list

Steps:
1. From previous state with 5 files
2. On host, delete a file: `rm /tmp/test-external/test.stl`
3. Click "Rescan"

Expected Results:
- [ ] Toast: "Scan complete: +0, ~0, -1"
- [ ] test.stl disappears from list
- [ ] File count shows "4" again

### Scenario 6: Path Inaccessibility ✅

**Expected**: Shows warning if path becomes inaccessible

Steps:
1. Keep external folder mounted
2. On host, remove permissions: `chmod 000 /tmp/test-external`
3. Click "Rescan"
4. Or wait for next folder tree load

Expected Results:
- [ ] Alert icon ⚠️ appears next to folder name
- [ ] Folder appears with red warning
- [ ] Rescan attempt shows error
- [ ] Files become inaccessible for download

### Scenario 7: Extension Filtering ✅

**Expected**: Only specified file types shown

Steps:
1. Create another external folder
2. Set extensions to: ".3mf,.gcode" (exclude .stl)
3. Mount same directory
4. Verify files shown

Expected Results:
- [ ] Only .3mf and .gcode files shown (3 files, not 4)
- [ ] test.stl is hidden
- [ ] nested.gcode is visible

### Scenario 8: Settings Management ✅

**Expected**: Settings persist and affect behavior

Steps:
1. Settings > File Manager tab
2. Change max_scan_depth to: 1
3. Create external folder with new path
4. Rescan

Expected Results:
- [ ] Rescan respects new depth (nested files may not appear)
- [ ] Change max_scan_depth back to: 10
- [ ] Rescan shows nested files again
- [ ] Settings auto-save without page reload

### Scenario 9: Multiple External Folders ✅

**Expected**: Can mount multiple directories

Steps:
1. Create subdir: `mkdir /tmp/external2`
2. Add files to it
3. Mount via External Folder button
4. Create second external folder

Expected Results:
- [ ] Both folders appear in tree
- [ ] Can switch between them
- [ ] File lists update correctly
- [ ] Rescan works independently for each

### Scenario 10: Security Validation ✅

**Expected**: Invalid paths are rejected

Steps:
1. Try to create external folder with:
   - Path: `/etc` (system directory)
   - Expected: "Cannot mount system directories"

2. Try path: `/invalid/path`
   - Expected: "Path does not exist"

3. Try path: `/home/user` (outside allowed paths)
   - Expected: "Path is not within allowed directories"

Expected Results:
- [ ] All invalid paths rejected with clear error
- [ ] Create button remains disabled
- [ ] No folders created for invalid paths

---

## 🔧 API Testing (Optional)

### Test Via curl

```bash
# Validate path
curl -X POST http://localhost:8000/api/v1/library/folders/external/validate \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/external"}' | jq

# Expected response:
# {
#   "valid": true,
#   "accessible": true,
#   "file_count": 4,
#   "directory_size_mb": 0.0
# }

# Create external folder
curl -X POST http://localhost:8000/api/v1/library/folders/external \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test External",
    "external_path": "/mnt/external",
    "external_readonly": true,
    "external_extensions": ".3mf,.stl"
  }' | jq

# Scan folder (assuming ID 1)
curl -X POST http://localhost:8000/api/v1/library/folders/1/scan \
  -H "Content-Type: application/json" | jq
```

---

## 🐛 Debugging Tips

### Enable verbose logging
```bash
DEBUG=true LOG_LEVEL=DEBUG docker compose up -d
docker compose logs -f | grep -i external
```

### Check database
```bash
# Access SQLite
sqlite3 app/data/bambuddy.db

# View external folders
SELECT id, name, is_external, external_path, external_readonly FROM library_folders WHERE is_external = 1;

# View external files
SELECT id, filename, is_external FROM library_files WHERE is_external = 1 LIMIT 10;
```

### Check file system
```bash
# Verify mount
docker exec bambuddy ls -la /mnt/external

# Check permissions
docker exec bambuddy stat /mnt/external
```

---

## ✅ Test Results Template

```
Date: ___________
Tester: ___________
Bambuddy Version: ___________

Feature: External Folder Mounting

Test Results:
- [ ] Path Validation: _____ (PASS/FAIL)
- [ ] Create Folder: _____ (PASS/FAIL)
- [ ] Browse Files: _____ (PASS/FAIL)
- [ ] Rescan Folder: _____ (PASS/FAIL)
- [ ] Detect Changes: _____ (PASS/FAIL)
- [ ] Handle Errors: _____ (PASS/FAIL)
- [ ] Extension Filter: _____ (PASS/FAIL)
- [ ] Settings: _____ (PASS/FAIL)
- [ ] Multiple Folders: _____ (PASS/FAIL)
- [ ] Security: _____ (PASS/FAIL)

Issues Found:
1. ___________
2. ___________

Notes:
___________
```

---

## 📊 Performance Testing

### Large Directory Test

```bash
# Create 1000 test files
mkdir -p /tmp/large-external
cd /tmp/large-external
for i in {1..1000}; do echo "test" > "file_${i}.3mf"; done

# Mount and test
# Expected: Scan completes in < 10 seconds
```

### Deep Directory Test

```bash
# Create nested structure
mkdir -p /tmp/deep-external
for i in {1..10}; do mkdir -p /tmp/deep-external/level${i}/sub; done
for f in /tmp/deep-external/level*/*.3mf; do echo "test" > "$f"; done

# Mount with max_scan_depth=10
# Expected: All files found recursively
```

---

## 🚨 Known Issues (Track During Testing)

- [ ] No issues found yet - please report!

---

## 📝 Testing Checklist for Release

Before marking feature as "ready for production":

- [ ] All 10 scenarios pass
- [ ] No data loss on rescan
- [ ] Error messages are clear
- [ ] UI responsive with large directories
- [ ] Read-only protection works
- [ ] Security validation blocks invalid paths
- [ ] Multiple folders work independently
- [ ] Settings changes apply immediately
- [ ] No memory leaks on repeated rescans
- [ ] Permissions are respected

---

## 📞 Issues & Reports

When reporting issues, include:
1. Steps to reproduce
2. Expected vs actual behavior
3. Error messages/logs
4. Browser/OS information
5. Directory structure (if relevant)

---

**Happy Testing! 🎉**

For issues: Please report in GitHub issue #124
For questions: Check EXTERNAL_FOLDERS_IMPLEMENTATION.md
