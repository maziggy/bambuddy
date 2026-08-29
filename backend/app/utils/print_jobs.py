"""Telling the printer's own internal jobs apart from a user's print.

Bambu firmware runs jobs on its own behalf -- bed levelling, vibration
compensation, the pressure-advance line it lays down before a print when flow
dynamics calibration is on -- and reports them over MQTT through exactly the
same print-start and print-complete events a real print uses. Nothing about the
event says "this one is mine": Bambuddy has to recognise the job by name.

Getting that wrong is not free. An unrecognised calibration run has no 3MF
anywhere on the printer, so the archive path sweeps FTP for a file that cannot
exist -- six candidate names across five directories, with retries -- and then
writes a no-3MF archive named after the calibration, on a printer that is in
the middle of calibrating.

Kept as a leaf module with no imports of its own so both the print-start and
print-complete callbacks can share one answer.
"""

# Job names the printer runs for itself. Matched exactly (after normalising),
# not by prefix or substring: "auto" and "calib" are ordinary words in a user's
# own filenames, and a rule loose enough to catch an unnamed future calibration
# would silently swallow somebody's print.
#
# ``auto_cali_for_user`` is the bed-levelling / vibration run, normally reported
# with a ``/usr/etc/print/`` path that the rule below catches on its own; it is
# listed anyway because the path is not guaranteed and a name-only report of it
# would otherwise slip through.
#
# ``auto_pa_line_calib_mode`` is the pressure-advance (K profile) line. This one
# is reported as a *subtask name* with no ``/usr/`` path at all, which is why
# the path rule alone was never enough.
#
# ``pa_line_calib_mode`` and ``pa_pattern_calib_mode`` are the same calibration
# started by hand rather than automatically before a print. Manual flow dynamics
# offers both shapes -- a line and a pattern -- and each reports under its own
# name with no ``auto_`` prefix, so neither is covered by the automatic entry
# above and both produced the same no-3MF archive. They are listed as two
# literals rather than matched by a shared ``pa_`` stem for the reason the whole
# set is exact: a stem rule would also swallow a user's own ``pa_bracket.3mf``.
INTERNAL_JOB_NAMES = frozenset(
    {
        "auto_cali_for_user",
        "auto_pa_line_calib_mode",
        "pa_line_calib_mode",
        "pa_pattern_calib_mode",
    }
)

# Longest first: ``.gcode.3mf`` has to be stripped whole, or ``.3mf`` would
# match first and leave a trailing ``.gcode`` behind.
_PRINT_SUFFIXES = (".gcode.3mf", ".gcode", ".3mf")


def _normalize_job_name(value: str) -> str:
    """Reduce a reported name to something comparable against the set above.

    Drops any directory part, one print-file suffix, and case. The printer is
    not consistent about which of these it includes -- the same calibration
    reports as a bare name in ``subtask_name`` and, when it appears at all, as
    a full path in ``gcode_file``.
    """
    name = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip().casefold()
    for suffix in _PRINT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def is_internal_printer_job(filename: str | None, subtask_name: str | None = None) -> bool:
    """True when this print event belongs to the printer, not to a user.

    Both fields are tested because neither is reliably populated: the
    pressure-advance line arrives as a subtask name with no filename, while the
    levelling run arrives as a ``/usr/etc/print/...`` path. A job is internal if
    *either* field says so.
    """
    if filename and filename.startswith("/usr/"):
        # Bambu keeps its own calibration gcode on the read-only system
        # partition. Nothing a user can print ever lives there, so the whole
        # prefix is safe to treat as internal without naming each file.
        return True
    return any(_normalize_job_name(value) in INTERNAL_JOB_NAMES for value in (filename, subtask_name) if value)
