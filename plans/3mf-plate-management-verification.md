# Plate Management Verification Checklist

## Manual Verification
- Open an archive with a multi-plate 3MF and verify the plate selector shows All Plates plus each plate entry.
- Select each plate and confirm the 3D viewer updates to show only that plate’s objects.
- Switch back to All Plates and confirm all geometry renders again.
- Verify the plate thumbnail and plate name are shown in the selector for plates with metadata.
- Open a single-plate 3MF and confirm the selector does not appear.
- Open an STL or non-3MF archive and confirm the viewer shows a friendly unsupported format error (no crash).

## Test Targets
- MSW handler for /api/v1/archives/:id/plates should return a structured response.
- ArchivesPage plate hover handlers should not throw when plates data is empty.
