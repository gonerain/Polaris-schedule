"""Schedule plugin registration, local data and live authority boundaries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins' / 'schedule'


class ScheduleComponentTests(unittest.TestCase):
    def setUp(self):
        from polaris_platform.bootstrap import build, default_config
        from polaris_platform.component import Component
        from polaris_platform.bootstrap import load_factory
        self.directory = tempfile.TemporaryDirectory()
        manifest = json.loads((PLUGIN / 'manifest.json').read_text())
        manifest['entry']['kwargs'] = {'data_dir': self.directory.name}
        self.runtime = build(default_config())
        self.runtime.install({'id': 'user', 'name': 'User', 'version': '1', 'platformApi': 1,
                              'entry': {'mode': 'inprocess'}}, Component)
        self.runtime.start_component('user')
        self.runtime.install(manifest, load_factory(manifest, PLUGIN, self.runtime))
        self.runtime.start_component('org.polaris.schedule')
        self.identity = manifest['id']
        for name in self.runtime.status(self.identity)['description']['operations']:
            self.runtime.grant('user', self.identity, name)
        self.context = self.runtime.user_context('user')

    def tearDown(self):
        for record in reversed(self.runtime.snapshot()):
            if record['state'] == 'running':
                self.runtime.stop_component(record['id'])
        self.directory.cleanup()

    def call(self, operation, **params):
        return self.runtime.client('user').call(self.identity, operation, params, context=self.context)

    def test_application_binding_metadata_for_business_operations(self):
        ops = self.runtime.status(self.identity)['description']['operations']
        names = ('get', 'time_context', 'tasks', 'add', 'update', 'delete', 'undo_delete',
                 'add_subtask', 'update_subtask', 'delete_subtask', 'reorder', 'move_overdue', 'organize_day')
        for name in names:
            self.assertEqual(ops[name]['applicationBinding'], 'instance')
            self.assertEqual(ops[name]['provides'], 'polaris.schedule.' + name.replace('_', '-') + '@1')
        self.assertNotIn('applicationBinding', ops['create_view'])

    def test_time_context_and_discoverable_business_contracts(self):
        from datetime import datetime, timezone, timedelta
        from polaris_schedule.clock import LocalClock
        class FakeClock(LocalClock):
            value = datetime(2026, 11, 4, 23, 59, tzinfo=timezone(timedelta(hours=8)))
            calls = 0
            def now(self):
                self.calls += 1
                return self.value
        component = self.runtime.component_instance(self.identity)
        clock = FakeClock()
        component.clock = component.store.clock = clock
        time = self.call('time_context')
        self.assertEqual(clock.calls, 1)
        self.assertEqual(time['now'], '2026-11-04T23:59:00+08:00')
        self.assertEqual(time['date'], '2026-11-04')
        self.assertEqual(time['timezone']['utcOffsetMinutes'], 480)
        self.assertEqual(self.call('get')['timeContext'], time)
        task = self.call('add', title='Due yesterday', date='2026-11-04')
        clock.value = datetime(2026, 11, 5, 0, 1, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(self.call('time_context')['timezone']['utcOffsetMinutes'], 540)
        self.assertEqual(self.call('get')['timeContext']['date'], '2026-11-05')
        self.assertEqual([t['id'] for t in self.call('move_overdue', target_date='2026-11-06')], [task['id']])
        # Explicit historical reference dates still take precedence.
        self.assertEqual(self.call('move_overdue', target_date='2026-11-08', today='2026-11-01'), [])
        description = component.describe()
        for name, meta in description['operations'].items():
            if meta.get('profile') == 'qt-native':
                continue
            self.assertTrue(meta['description'])
            self.assertEqual(meta['parameters']['type'], 'object')
            self.assertFalse(meta['parameters']['additionalProperties'])
        self.assertEqual(description['operations']['update']['parameters']['required'], ['identity'])
        description['operations']['update']['parameters']['properties'].clear()
        self.assertIn('identity', component.describe()['operations']['update']['parameters']['properties'])
        child = self.call('add_subtask', identity=task['id'], title='Step')
        self.call('update_subtask', parent_id=task['id'], subtask_id=child['id'], done=True)
        self.assertTrue(self.call('tasks', date='2026-11-06')[0]['subtasks'][0]['done'])

    def test_business_without_view_and_restart_persistence(self):
        task = self.call('add', title='Plan', date='2026-10-30')
        self.call('add_subtask', identity=task['id'], title='First step')
        self.call('update', identity=task['id'], important=True, notes='Keep this')
        self.assertEqual(self.call('get')['tasks'][0]['notes'], 'Keep this')
        self.assertEqual(self.call('release_view', handle='missing'), {'released': False})
        self.runtime.stop_component(self.identity)
        self.runtime.start_component(self.identity)
        self.context = self.runtime.user_context('user')
        restored = self.call('get')['tasks'][0]
        self.assertTrue(restored['important'])
        self.assertEqual(restored['subtasks'][0]['title'], 'First step')

    def test_revoked_mutation_and_read_do_not_bypass_context(self):
        from polaris_platform.protocol import PlatformError
        self.call('add', title='Before')
        self.runtime.revoke('user', self.identity, 'add')
        with self.assertRaises(PlatformError):
            self.call('add', title='Denied')
        self.assertEqual(len(self.call('get')['tasks']), 1)
        self.runtime.revoke('user', self.identity, 'get')
        with self.assertRaises(PlatformError):
            self.call('get')

    def test_descriptor_is_generic_native_contract(self):
        description = self.runtime.status(self.identity)['description']
        self.assertEqual(description['dependencies'], [])
        self.assertEqual(description['views'][0]['adapter'], 'qt-quick-item')
        self.assertEqual(description['operations']['create_view']['profile'], 'qt-native')

    def test_import_and_business_are_qt_lazy(self):
        code = ("import sys,tempfile;sys.path[:0]=" + repr([str(ROOT), str(PLUGIN)]) + ";"
                "from polaris_schedule import create;"
                "d=tempfile.TemporaryDirectory();c=create(data_dir=d.name);c.get();"
                "assert not any(k.startswith('PySide6') for k in sys.modules)")
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_schedule_config_starts_headless_business(self):
        from polaris_platform.bootstrap import build
        config = json.loads((ROOT / 'config.schedule.json').read_text())
        self.assertEqual(len(config['components']), 4)
        config['components'][-1] = {'directory': str(PLUGIN), 'manifest': json.loads((PLUGIN / 'manifest.json').read_text()), 'autostart': True}
        config['components'][-1]['manifest']['entry']['kwargs'] = {'data_dir': self.directory.name}
        runtime = build(config, ROOT)
        try:
            self.assertEqual(runtime.status(self.identity)['state'], 'running')
            installed = next(row for row in runtime.export_installations()
                             if row['manifest']['id'] == self.identity)
            self.assertTrue(installed['autostart'])
            self.assertNotIn('defer', installed['manifest']['entry'])
        finally:
            for record in reversed(runtime.snapshot()):
                if record['state'] == 'running':
                    runtime.stop_component(record['id'])


if __name__ == '__main__':
    unittest.main()
