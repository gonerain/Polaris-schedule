"""Real pointer/keyboard regression coverage for the schedule's owned Qt view."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

if __name__ != '__main__':
    class ScheduleUIProcessTests(unittest.TestCase):
        def test_schedule_interactions(self):
            import importlib.util
            if importlib.util.find_spec('PySide6') is None:
                self.skipTest('Optional PySide6 is not installed')
            result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                    env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
                                    capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
else:
    import tempfile
    from PySide6.QtCore import QPoint, QPointF, Qt, QUrl, QEvent, QCoreApplication
    from PySide6.QtGui import QGuiApplication, QCursor, QKeyEvent
    from PySide6.QtQuick import QQuickView
    from PySide6.QtTest import QTest
    import shiboken6
    from polaris_platform.bootstrap import build, default_config
    from polaris_platform.component import Component
    import json
    EXPORTED = os.environ.get('POLARIS_TEST_SCHEDULE_EXPORT')
    exported = json.loads(Path(EXPORTED).read_text()) if EXPORTED else None
    sys.path.insert(0, exported['directory'] if exported else str(ROOT / 'plugins' / 'schedule'))
    from polaris_schedule import Schedule

    def find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = find(child, name)
            if found is not None:
                return found
        return None

    class ScheduleInteractions(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.app = QGuiApplication.instance() or QGuiApplication([])

        def setUp(self):
            self.directory = tempfile.TemporaryDirectory()
            self.runtime = build(default_config())
            self.runtime.install({'id': 'user', 'name': 'User', 'version': '1', 'platformApi': 1,
                                  'entry': {'mode': 'inprocess'}}, Component)
            self.runtime.start_component('user')
            self.manifest = {'id': 'org.polaris.schedule', 'name': 'Schedule', 'version': '1', 'platformApi': 1,
                             'entry': {'mode': 'inprocess'}}
            if exported:
                from copy import deepcopy
                from polaris_platform.bootstrap import load_factory
                import polaris_schedule
                self.manifest = deepcopy(exported['manifest'])
                factory_manifest = deepcopy(self.manifest)
                factory_manifest['entry']['kwargs'] = dict(data_dir=self.directory.name)
                self.component = load_factory(factory_manifest, exported['directory'], self.runtime)()
                self.assertEqual(Path(polaris_schedule.__file__).parent, Path(exported['directory']) / 'polaris_schedule')
            else:
                self.component = Schedule(self.directory.name)
            self.runtime.install(self.manifest, lambda: self.component)
            self.runtime.start_component('org.polaris.schedule')
            for operation in self.runtime.status('org.polaris.schedule')['description']['operations']:
                self.runtime.grant('user', 'org.polaris.schedule', operation)
            self.context = self.runtime.user_context('user')
            source = Path(self.directory.name) / 'root.qml'
            source.write_text("import QtQuick\nRectangle { width: 780; height: 620; color: '#f8faff' }\n")
            self.window = QQuickView()
            self.window.setSource(QUrl.fromLocalFile(str(source)))
            self.window.resize(780, 620)
            self.window.show()
            self.resource = self.call('create_view', {'view': 'main', 'parent': self.window.rootObject()})
            self.item = self.resource['item']
            if exported:
                from PySide6.QtQml import QQmlComponent
                definition = self.item.findChild(QQmlComponent)
                self.assertEqual(Path(definition.url().toLocalFile()), Path(exported['directory']) / 'polaris_schedule/Schedule.qml')
            self.item.setWidth(780)
            self.item.setHeight(620)
            QTest.qWait(80)

        def tearDown(self):
            for row in reversed(self.runtime.snapshot()):
                if row['state'] == 'running':
                    self.runtime.stop_component(row['id'])
            self.window.close()
            shiboken6.delete(self.window)
            self.app.processEvents()
            self.directory.cleanup()

        def call(self, operation, params=None):
            return self.runtime.client('user').call('org.polaris.schedule', operation, params, context=self.context)

        def control(self, name):
            result = find(self.window.contentItem(), name)
            self.assertIsNotNone(result, name)
            return result

        def click(self, target):
            item = self.control(target) if isinstance(target, str) else target
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            point = QPoint(round(point.x()), round(point.y()))
            QCursor.setPos(self.window.mapToGlobal(point))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, point)
            QTest.qWait(35)

        def type(self, name, value):
            self.click(name)
            QTest.keyClick(self.window, Qt.Key_A, Qt.ControlModifier)
            for char in value:
                QCoreApplication.sendEvent(self.window, QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, char))
                QCoreApplication.sendEvent(self.window, QKeyEvent(QEvent.KeyRelease, 0, Qt.NoModifier, char))
            QTest.qWait(20)

        def test_direct_navigation_reschedule_reorder_and_planner_capture(self):
            self.click('nav_收集箱')
            self.type('quickAdd', 'Capture first')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            self.type('quickAdd', 'Capture second')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            self.assertTrue(self.control('quickAdd').hasActiveFocus())
            tasks = self.call('get')['tasks']
            self.runtime.revoke('user', 'org.polaris.schedule', 'reorder')
            self.click('taskActions_1')
            self.click('moveUp_1')
            self.assertEqual(self.call('get')['tasks'][0]['title'], 'Capture first')
            self.runtime.grant('user', 'org.polaris.schedule', 'reorder')
            self.click('taskActions_1')
            self.click('moveUp_1')
            QTest.qWait(40)
            self.assertEqual(self.call('get')['tasks'][0]['title'], 'Capture second', self.component._presentation._views[self.resource['handle']][2].lastError)
            self.click('taskActions_0')
            self.click('planTomorrow_0')
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.assertEqual(self.call('get')['tasks'][0]['date'], self.item.currentDay(1))
            self.click('mode_1')
            target = self.item.property('selectedDate')
            self.click('addDay_' + target)
            self.type('quickAdd', 'Day column capture')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            self.assertEqual(next(t for t in self.call('get')['tasks'] if t['title'] == 'Day column capture')['date'], target)
            # Drag the remaining inbox task with the real handle to a date column.
            handle = self.control('pending_dragTask_0')
            drop = self.control('dropDay_' + target)
            start = handle.mapToScene(QPointF(handle.width()/2, handle.height()/2))
            end = drop.mapToScene(QPointF(drop.width()/2, drop.height()/2))
            QTest.mousePress(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(start.x()), round(start.y())))
            for i in range(1, 13):
                point = start + (end - start) * (i/12)
                QTest.mouseMove(self.window, QPoint(round(point.x()), round(point.y())), 20)
            QTest.mouseRelease(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(end.x()), round(end.y())))
            QTest.qWait(80)
            self.assertEqual(next(t for t in self.call('get')['tasks'] if t['title'] == 'Capture first')['date'], target)
            self.window.grabWindow().save('/tmp/polaris-schedule-planning-new.png')
            self.click('mode_0')
            self.click('nav_我的一天')
            self.type('quickAdd', 'Keyboard draft')
            QTest.keyClick(self.window, Qt.Key_F, Qt.ControlModifier)
            QTest.qWait(30)
            self.assertTrue(self.control('scheduleSearch').hasActiveFocus())
            QTest.keyClick(self.window, Qt.Key_N, Qt.ControlModifier)
            QTest.qWait(30)
            self.assertTrue(self.control('quickAdd').hasActiveFocus())
            self.assertEqual(self.control('quickAdd').property('text'), 'Keyboard draft')

        def test_day_rollover_follows_today_but_keeps_explicit_browsing(self):
            self.item.setProperty('todayDate', '2026-10-31')
            self.item.setProperty('selectedDate', '2026-10-31')
            self.item.refreshToday('2026-11-01')
            self.assertEqual(self.item.property('selectedDate'), '2026-11-01')
            self.item.setProperty('selectedDate', '2026-12-25')
            self.item.refreshToday('2026-11-02')
            self.assertEqual(self.item.property('selectedDate'), '2026-12-25')
            self.item.setWidth(370)
            self.item.setHeight(220)
            self.assertTrue(self.control('todayButton').property('visible'))

        def test_shared_clock_rollover_buttons_and_permission_errors(self):
            from datetime import datetime, timezone, timedelta
            from polaris_schedule.clock import LocalClock
            class FakeClock(LocalClock):
                value = datetime(2026, 11, 4, 23, 59, tzinfo=timezone(timedelta(hours=8)))
                def now(self):
                    return self.value
            clock = FakeClock()
            self.component.clock = self.component.store.clock = clock
            bridge = self.component._presentation._views[self.resource['handle']][2]
            self.assertTrue(bridge.refreshTime())
            self.item.selectToday()
            self.assertEqual(self.item.property('selectedDate'), '2026-11-04')
            clock.value = datetime(2026, 11, 5, 0, 1, tzinfo=timezone(timedelta(hours=9)))
            QTest.qWait(1100)  # Exercise the actual periodic refresh after a clock jump.
            self.assertEqual(self.item.property('selectedDate'), '2026-11-05')
            self.item.setProperty('selectedDate', '2026-12-25')
            clock.value = datetime(2026, 11, 6, 0, 1, tzinfo=timezone.utc)
            self.assertTrue(bridge.refreshTime())
            self.assertEqual(self.item.property('selectedDate'), '2026-12-25')
            self.click('todayButton')
            self.assertEqual(self.item.property('selectedDate'), '2026-11-06')
            task = self.call('add', {'title': 'Clock task', 'date': '2026-11-06'})
            QTest.qWait(40)
            self.item.openTask(task)
            QTest.qWait(40)
            self.click('rescheduleTomorrow')
            self.assertEqual(self.control('taskDate').property('text'), '2026-11-07')
            self.click('rescheduleToday')
            self.assertEqual(self.control('taskDate').property('text'), '2026-11-06')
            self.runtime.revoke('user', 'org.polaris.schedule', 'update')
            self.click('saveTask')
            failed = bridge.lastError
            self.assertTrue(failed)
            self.assertTrue(bridge.refreshTime())
            self.assertEqual(bridge.lastError, failed)
            self.runtime.revoke('user', 'org.polaris.schedule', 'time_context')
            self.assertFalse(bridge.refreshTime())
            self.assertEqual(self.item.property('todayDate'), '2026-11-06')
            self.runtime.grant('user', 'org.polaris.schedule', 'time_context')
            self.assertTrue(bridge.refreshTime())
            self.assertEqual(bridge.lastError, failed)

        def test_compact_planner_restores_daily_navigation(self):
            for mode in ('周', '月'):
                self.item.setWidth(780)
                self.item.setHeight(620)
                self.item.setProperty('selectedDate', '2026-11-04')
                self.item.setProperty('presentation', mode)
                self.item.setWidth(370)
                self.item.setHeight(220)
                QTest.qWait(30)
                self.assertEqual(self.item.property('presentation'), '列表')
                self.assertEqual(self.control('selectedDay').property('text'), '11 / 04')
                self.click('nextDay')
                self.assertEqual(self.item.property('selectedDate'), '2026-11-05')

        def open_fixture_editor(self, date='2026-12-31', time='09:37'):
            self.item.setProperty('selectedDate', date)
            task = self.call('add', dict(title='Picker fixture', date=date, time=time))
            QTest.qWait(40)
            row = self.control('completeTask_0').parentItem()
            point = row.mapToScene(QPointF(140, 18))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            QTest.qWait(40)
            return task

        def open_time_options(self, name):
            combo = self.control(name)
            point = combo.mapToScene(QPointF(combo.width() - 10, combo.height() / 2))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            QTest.qWait(35)

        def test_time_picker_pointer_keyboard_clear_and_save(self):
            task = self.open_fixture_editor()
            self.click('openTimePicker')
            self.assertEqual(self.control('timePickerMinutes').property('editText'), '37')
            self.open_time_options('timePickerHours')
            self.click('timePickerHoursOption_10')
            self.open_time_options('timePickerMinutes')
            self.click('timePickerMinutesOption_3')
            self.click('applyPickedTime')
            self.assertEqual(self.control('taskTime').property('text'), '10:15')
            self.assertEqual(self.call('get')['tasks'][0]['time'], '09:37')
            self.click('openTimePicker')
            self.type('timePickerMinutes', '37')
            self.click('applyPickedTime')
            self.assertEqual(self.control('taskTime').property('text'), '10:37')
            self.click('saveTask')
            self.assertEqual(self.call('get')['tasks'][0]['time'], '10:37')
            # Full range remains available, independent of the five-minute list.
            for value in ('00:00', '23:59'):
                row = self.control('completeTask_0').parentItem()
                self.click(row)
                self.click('openTimePicker')
                self.type('timePickerHours', value[:2])
                self.type('timePickerMinutes', value[3:])
                self.click('applyPickedTime')
                self.assertEqual(self.control('taskTime').property('text'), value)
                self.click('saveTask')
                self.assertEqual(self.call('get')['tasks'][0]['time'], value)
            self.click(self.control('completeTask_0').parentItem())
            self.click('openTimePicker')
            self.window.grabWindow().save('/tmp/polaris-schedule-time-picker.png')
            self.type('timePickerMinutes', '12')
            self.click('cancelPickedTime')
            self.assertEqual(self.control('taskTime').property('text'), '23:59')
            self.click('openTimePicker')
            self.assertEqual(self.control('timePickerMinutes').property('editText'), '59')
            self.click('clearPickedTime')
            self.assertEqual(self.control('taskTime').property('text'), '')
            self.click('saveTask')
            self.assertEqual(self.call('get')['tasks'][0]['time'], '')
            self.assertEqual(Schedule(self.directory.name).store.tasks()[0]['time'], '')

        def test_date_picker_pointer_draft_today_and_narrow_editor(self):
            self.open_fixture_editor()
            self.click('openDatePicker')
            self.click('datePickerNextMonth')
            self.click('datePickerDay_2027-01-15')
            self.click('datePickerCancel')
            self.assertEqual(self.control('taskDate').property('text'), '2026-12-31')
            self.click('openDatePicker')
            self.assertTrue(self.control('datePickerDay_2026-12-31').isVisible())
            self.click('datePickerNextMonth')
            self.click('datePickerDay_2027-01-15')
            self.click('datePickerConfirm')
            self.assertEqual(self.control('taskDate').property('text'), '2027-01-15')
            self.assertEqual(self.call('get')['tasks'][0]['date'], '2026-12-31')
            self.type('taskDate', '2028-02-01')
            self.click('openDatePicker')
            self.click('datePickerDay_2028-02-29')
            self.click('datePickerConfirm')
            self.assertEqual(self.control('taskDate').property('text'), '2028-02-29')
            self.window.resize(370, 360); self.item.setWidth(370); self.item.setHeight(360)
            QTest.qWait(40)
            self.click('openDatePicker')
            self.window.grabWindow().save('/tmp/polaris-schedule-date-picker.png')
            self.click('datePickerToday')
            self.click('datePickerConfirm')
            self.assertEqual(self.control('taskDate').property('text'), self.item.property('todayDate'))
            self.click('saveTask')
            self.assertEqual(self.call('get')['tasks'][0]['date'], self.item.property('todayDate'))
            self.item.setProperty('selectedDate', self.item.property('todayDate'))
            QTest.qWait(30)
            self.click(self.control('completeTask_0').parentItem())
            self.click('rescheduleInbox')
            self.assertEqual(self.control('taskDate').property('text'), '')
            self.assertEqual(self.control('taskTime').property('text'), '')
            self.click('saveTask')
            saved = self.call('get')['tasks'][0]
            self.assertIsNone(saved['date'])
            self.assertEqual(saved['time'], '')

        def test_crud_editor_subtask_restart_and_revocation(self):
            self.type('quickAdd', '<b>Planning</b>')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            task = self.call('get')['tasks'][0]
            self.assertEqual(task['title'], '<b>Planning</b>')
            self.click('importantTask_0')
            self.assertTrue(self.call('get')['tasks'][0]['important'])
            # Open through the visible task row, outside the completion/star buttons.
            row = self.control('completeTask_0').parentItem()
            point = row.mapToScene(QPointF(150, 18))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            QTest.qWait(50)
            self.type('taskTitle', 'Planning revised')
            self.type('taskTime', '09:30')
            self.type('taskNotes', 'Release checklist')
            self.type('subtaskInput', 'Review patch')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            self.assertEqual(self.call('get')['tasks'][0]['subtasks'][0]['title'], 'Review patch')
            self.type('subtaskTitle_0', 'Review revised patch')
            self.click('completeSubtask_0')
            self.window.grabWindow().save('/tmp/polaris-schedule-editor.png')
            self.runtime.revoke('user', 'org.polaris.schedule', 'update')
            self.click('saveTask')
            self.assertEqual(self.control('taskTitle').property('text'), 'Planning revised')
            self.runtime.grant('user', 'org.polaris.schedule', 'update')
            self.click('saveTask')
            task = self.call('get')['tasks'][0]
            self.assertEqual((task['title'], task['time'], task['notes']), ('Planning revised', '09:30', 'Release checklist'))
            self.assertTrue(task['subtasks'][0]['done'])
            self.assertEqual(task['subtasks'][0]['title'], 'Review revised patch')
            self.assertEqual(Schedule(self.directory.name).store.tasks()[0], task)
            self.window.grabWindow().save('/tmp/polaris-schedule-wide.png')
            self.item.setWidth(370); self.item.setHeight(220)
            self.window.resize(370, 220)
            QTest.qWait(60)
            self.window.grabWindow().save('/tmp/polaris-schedule-compact.png')
            self.runtime.revoke('user', 'org.polaris.schedule', 'add')
            self.type('quickAdd', 'Keep this draft')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            self.assertEqual(self.control('quickAdd').property('text'), 'Keep this draft')
            self.assertEqual(len(self.call('get')['tasks']), 1)
            self.runtime.grant('user', 'org.polaris.schedule', 'add')
            self.click('addTask')
            self.assertEqual(len(self.call('get')['tasks']), 2)
            # Delete through the compact editor and undo through the card.
            row = self.control('completeTask_0').parentItem()
            point = row.mapToScene(QPointF(150, 18))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            QTest.qWait(40)
            self.click('deleteTask')
            self.assertEqual(len(self.call('get')['tasks']), 1)
            self.click('undoDelete')
            self.assertEqual(len(self.call('get')['tasks']), 2)
            self.click('completeTask_0')
            self.assertTrue(any(t['done'] for t in self.call('get')['tasks']))

        def test_failed_parent_error_survives_queued_child_refresh(self):
            self.type('quickAdd', 'Parent task')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            task = self.call('get')['tasks'][0]
            self.call('add_subtask', {'identity': task['id'], 'title': 'Original child'})
            QTest.qWait(40)
            row = self.control('completeTask_0').parentItem()
            point = row.mapToScene(QPointF(150, 18))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            QTest.qWait(40)
            self.type('taskTitle', '')
            QTest.keyClick(self.window, Qt.Key_Backspace)
            self.type('subtaskTitle_0', 'Child before failed save')
            # Enter commits the child and queues a cross-view refresh. Save the
            # invalid parent before processing that refresh's event delivery.
            QTest.keyClick(self.window, Qt.Key_Return)
            self.click('saveTask')
            QTest.qWait(100)
            self.assertIn('标题不能为空', self.control('editorError').property('text'))
            self.assertEqual(self.control('taskTitle').property('text'), '')
            self.assertEqual(self.call('get')['tasks'][0]['title'], 'Parent task')
            self.type('taskTitle', 'Parent revised')
            self.type('taskDate', '2026-02-30')
            self.type('subtaskTitle_0', 'Child before invalid date')
            QTest.keyClick(self.window, Qt.Key_Return)
            self.click('saveTask')
            QTest.qWait(100)
            self.assertIn('日期', self.control('editorError').property('text'))
            self.assertEqual(self.control('taskTitle').property('text'), 'Parent revised')
            self.assertEqual(self.control('taskDate').property('text'), '2026-02-30')
            self.assertEqual(self.call('get')['tasks'][0]['title'], 'Parent task')

        def test_overdue_scope_and_subtask_notes_search(self):
            self.call('add', {'title': 'Inbox', 'date': None})
            past = self.call('add', {'title': 'Overdue', 'date': '2001-01-01'})
            finished = self.call('add', {'title': 'Finished', 'date': '2001-01-01'})
            self.call('update', {'identity': finished['id'], 'done': True})
            child = self.call('add_subtask', {'identity': past['id'], 'title': 'Checklist'})
            self.call('update_subtask', {'parent_id': past['id'], 'subtask_id': child['id'], 'notes': 'buried keyword'})
            QTest.qWait(60)
            self.click('nav_待重新安排')
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.click('nav_收集箱')
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.click('searchButton')
            self.type('scheduleSearch', 'buried keyword')
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.click('searchButton')
            self.assertEqual(self.control('taskList').property('count'), 1)

        def test_search_planner_and_permission_boundaries(self):
            for title in ('Draft proposal', 'Review design', 'Publish notes'):
                self.type('quickAdd', title)
                QTest.keyClick(self.window, Qt.Key_Return)
                QTest.qWait(40)
            for title in ('Prepare agenda', 'Team meeting'):
                self.click('nextDay')
                self.type('quickAdd', title)
                QTest.keyClick(self.window, Qt.Key_Return)
                QTest.qWait(40)
            self.click('previousDay')
            self.click('previousDay')
            self.click('searchButton')
            self.type('scheduleSearch', 'Review')
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.click('searchButton')
            self.click('mode_1')
            self.window.grabWindow().save('/tmp/polaris-schedule-week.png')
            self.click('mode_2')
            self.click('selectedDay')
            self.type('dayInput', '2026-01-31')
            self.click('chooseDay')
            self.click('nextDay')
            self.assertEqual(self.item.property('selectedDate'), '2026-02-28')
            self.click('selectedDay')
            self.type('dayInput', '2026-02-30')
            self.click('chooseDay')
            self.assertEqual(self.item.property('selectedDate'), '2026-02-28')
            QTest.keyClick(self.window, Qt.Key_Escape)
            self.click('selectedDay')
            from datetime import date
            self.type('dayInput', date.today().isoformat())
            self.click('chooseDay')
            self.window.grabWindow().save('/tmp/polaris-schedule-month.png')
            self.click('mode_0')
            self.click('nav_收集箱')
            self.type('quickAdd', 'Inbox task')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(40)
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.assertIsNone(self.call('get')['tasks'][-1]['date'])
            self.click('searchButton')
            self.type('scheduleSearch', 'Review')
            self.assertEqual(self.control('taskList').property('count'), 1)
            self.click('searchButton')
            # A business write succeeds even when its subsequent read is revoked.
            before = len(self.component.store.tasks())
            self.runtime.revoke('user', 'org.polaris.schedule', 'get')
            self.type('quickAdd', 'Accepted once')
            QTest.keyClick(self.window, Qt.Key_Return)
            QTest.qWait(60)
            self.assertEqual(self.control('quickAdd').property('text'), '')
            self.assertEqual(len(self.component.store.tasks()), before + 1)
            self.assertEqual(self.control('taskList').property('count'), 0)
            bridge = self.component._presentation._views[self.resource['handle']][2]
            self.assertTrue(bridge.lastError)
            self.runtime.grant('user', 'org.polaris.schedule', 'get')
            self.assertTrue(bridge.refresh())
            self.call('release_view', {'handle': self.resource['handle']})
            QTest.qWait(40)
            self.assertEqual(len(self.call('get')['tasks']), before + 1)
            self.assertFalse(self.window.rootObject().childItems())
            replacement = self.call('create_view', {'view': 'main', 'parent': self.window.rootObject()})
            self.assertIsNotNone(replacement['item'])
            self.runtime.stop_component('org.polaris.schedule')
            QTest.qWait(40)
            self.assertFalse(self.window.rootObject().childItems())

    unittest.main()
