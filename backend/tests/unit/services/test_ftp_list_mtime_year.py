"""An FTP LIST time without a year gets the right year on any host (DB-C8).

Recent entries list as "Oct 20 23:20" with no year. The year was taken from
the host's local clock, and an entry "in the future" was moved a year back.
Printers report these times in UTC (tested X1C/H2D firmware), so on a host
behind UTC every evening entry -- 23:20 UTC is still 19:20 in New York -- was
dated a year back, and its ipcam recording no longer matched its print.
"""

from datetime import datetime

from backend.app.services.bambu_ftp import _parse_list_mtime


def test_a_utc_time_later_than_the_hosts_local_clock_stays_this_year():
    # 19:30 in New York is 23:30 UTC; the printer's entry is 23:20 UTC.
    assert _parse_list_mtime("Oct", "20", "23:20", now_utc=datetime(2026, 10, 20, 23, 30)) == datetime(
        2026, 10, 20, 23, 20
    )


def test_firmware_on_local_time_a_few_hours_ahead_stays_this_year():
    # Some firmware lists printer-local time, up to 14 h ahead of UTC.
    assert _parse_list_mtime("Oct", "21", "09:00", now_utc=datetime(2026, 10, 20, 20, 0)).year == 2026


def test_a_date_months_ahead_is_last_year():
    assert _parse_list_mtime("Dec", "30", "10:15", now_utc=datetime(2026, 1, 5, 12, 0)) == datetime(
        2025, 12, 30, 10, 15
    )


def test_an_entry_with_a_year_keeps_it():
    assert _parse_list_mtime("Nov", "30", "2024", now_utc=datetime(2026, 10, 20)) == datetime(2024, 11, 30)


def test_garbage_is_no_time():
    assert _parse_list_mtime("Foo", "99", "xx:yy", now_utc=datetime(2026, 10, 20)) is None
