"""Time context is derived from a single timezone-aware sample."""
from datetime import datetime, timezone, timedelta
import unittest
from polaris_schedule.clock import LocalClock
from polaris_schedule.model import ScheduleStore


class ClockTests(unittest.TestCase):
    def test_context_samples_only_once_across_midnight(self):
        class Clock(LocalClock):
            calls = 0
            def now(self):
                self.calls += 1
                return datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-3, minutes=-30)))
        clock = Clock()
        context = clock.context()
        self.assertEqual(clock.calls, 1)
        self.assertEqual(context['date'], '2026-12-31')
        self.assertEqual(context['now'], '2026-12-31T23:59:59-03:30')
        self.assertEqual(context['timezone']['utcOffsetMinutes'], -210)
        self.assertIs(ScheduleStore(clock=clock).clock, clock)

    def test_naive_clock_is_rejected(self):
        class NaiveClock(LocalClock):
            def now(self):
                return datetime(2026, 11, 4)
        with self.assertRaises(ValueError):
            NaiveClock().context()
