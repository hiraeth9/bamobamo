"""Pure asynchronous tests; never load Hermes configuration or API clients."""
import asyncio
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hermes_director_cli as launcher


class AuxiliaryVisionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_success_preserves_original_result_and_arguments(self):
        response = json.dumps({'success': True, 'analysis': 'actual analysis', 'extra': [1, 2]})
        calls = []
        async def original(*args, **kwargs):
            calls.append((args, kwargs))
            return response
        guarded = launcher.guard_auxiliary_vision(original)
        result = await guarded('/frame.png', 'question', model='same-model', task_id='same-task', region=[0, 0, 2, 2])
        self.assertIs(result, response)
        self.assertEqual(calls, [(('/frame.png', 'question'), {'model': 'same-model', 'task_id': 'same-task', 'region': [0, 0, 2, 2]})])
        self.assertIs(await guarded('/next.png', 'next'), response)
        self.assertEqual(len(calls), 2)

    async def test_timeout_cancels_work_and_future_auxiliary_call_returns_immediately(self):
        cancelled = asyncio.Event()
        calls = []
        async def hanging(*args):
            calls.append(args)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        guarded = launcher.guard_auxiliary_vision(hanging, timeout_seconds=.01)
        first = json.loads(await guarded('first'))
        self.assertFalse(first['success'])
        self.assertEqual(first['error_code'], 'auxiliary_vision_timeout')
        self.assertTrue(cancelled.is_set())
        second = json.loads(await asyncio.wait_for(guarded('second'), timeout=.05))
        self.assertFalse(second['success'])
        self.assertEqual(second['error_code'], 'auxiliary_vision_circuit_open')
        self.assertEqual(calls, [('first',)])
        self.assertNotIn('visual_review_passed', first)

    async def test_explicit_failure_is_preserved_and_opens_the_circuit(self):
        for response in [{'success': False, 'error': 'upstream failed'},
                         json.dumps({'success': False, 'error': 'upstream failed'})]:
            with self.subTest(response_type=type(response).__name__):
                calls = []
                async def original(*args):
                    calls.append(args)
                    return response
                guarded = launcher.guard_auxiliary_vision(original)
                self.assertIs(await guarded('first'), response)
                second = json.loads(await guarded('second'))
                self.assertFalse(second['success'])
                self.assertEqual(second['error_code'], 'auxiliary_vision_circuit_open')
                self.assertEqual(calls, [('first',)])

    async def test_first_non_timeout_exception_is_honest_and_does_not_echo_private_text(self):
        calls = []
        async def broken(*args):
            calls.append(args)
            raise RuntimeError('sensitive-sdk-detail-for-test')
        guarded = launcher.guard_auxiliary_vision(broken)
        raw = await guarded('first')
        first = json.loads(raw)
        self.assertFalse(first['success'])
        self.assertEqual(first['error_code'], 'auxiliary_vision_exception')
        self.assertIn('RuntimeError', first['error'])
        self.assertNotIn('sensitive-sdk-detail-for-test', raw)
        self.assertFalse(json.loads(await guarded('second'))['success'])
        self.assertEqual(calls, [('first',)])

    async def test_caller_cancellation_propagates_without_disabling_later_calls(self):
        entered = asyncio.Event()
        async def original(mode):
            if mode == 'wait':
                entered.set()
                await asyncio.Event().wait()
            return {'success': True}
        guarded = launcher.guard_auxiliary_vision(original)
        task = asyncio.create_task(guarded('wait'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await guarded('fast'), {'success': True})

    async def test_install_keeps_native_handler_and_is_idempotent_after_auxiliary_failure(self):
        module = ModuleType('fake_vision')
        async def auxiliary(*args, **kwargs):
            return json.dumps({'success': False, 'error': 'auxiliary unavailable'})
        async def native(*args, **kwargs):
            return json.dumps({'success': True, 'native_vision': True})
        module.vision_analyze_tool = auxiliary
        module._vision_analyze_native = native
        module.use_native = False
        exec('async def _handle_vision_analyze(args):\n'
             '    if use_native:\n'
             '        return await _vision_analyze_native(args)\n'
             '    return await vision_analyze_tool(args)\n', module.__dict__)
        registered_handler = module._handle_vision_analyze
        wrapped = launcher.install_auxiliary_vision_guard(module)
        self.assertIs(module._handle_vision_analyze, registered_handler)
        self.assertFalse(json.loads(await registered_handler({}))['success'])
        self.assertIs(launcher.install_auxiliary_vision_guard(module), wrapped)
        self.assertEqual(json.loads(await registered_handler({}))['error_code'], 'auxiliary_vision_circuit_open')
        module.use_native = True
        self.assertEqual(json.loads(await registered_handler({})), {'success': True, 'native_vision': True})


class LauncherTests(unittest.TestCase):
    def test_original_main_initializes_before_wrapper_and_receives_all_arguments(self):
        original_argv = sys.argv
        original_path = list(sys.path)
        arguments = ['-z', '三个人\n举手，原文不删。', '--model', 'requested-model',
                     '--provider', 'requested-provider', '--toolsets', 'mcp,vision,terminal',
                     '--usage-file', '/usage with spaces.json']
        imports = []
        observed = []
        async def auxiliary(*args, **kwargs):
            return {'success': True}
        vision = SimpleNamespace(vision_analyze_tool=auxiliary)
        def original_main():
            observed.append(list(sys.argv))
            self.assertIsNot(vision.vision_analyze_tool, auxiliary)
            return 17
        def importer(name):
            imports.append(name)
            if name == 'hermes_cli.main':
                self.assertEqual(sys.argv, ['hermes', *arguments])
                return SimpleNamespace(main=original_main)
            if name == 'tools.vision_tools':
                self.assertEqual(imports, ['hermes_cli.main', 'tools.vision_tools'])
                return vision
            raise AssertionError('unexpected import: ' + name)
        with patch.object(launcher.importlib, 'import_module', side_effect=importer):
            self.assertEqual(launcher.main(arguments), 17)
        self.assertEqual(observed, [['hermes', *arguments]])
        self.assertIs(sys.argv, original_argv)
        self.assertEqual(sys.path, original_path)

    def test_default_arguments_and_system_exit_are_preserved(self):
        with patch.object(sys, 'argv', ['project_launcher', '-z', '完整提示词']):
            invocation = sys.argv
            async def auxiliary(*args):
                return {'success': True}
            vision = SimpleNamespace(vision_analyze_tool=auxiliary)
            def original_main():
                self.assertEqual(sys.argv, ['hermes', '-z', '完整提示词'])
                raise SystemExit(23)
            def importer(name):
                return SimpleNamespace(main=original_main) if name == 'hermes_cli.main' else vision
            with patch.object(launcher.importlib, 'import_module', side_effect=importer):
                with self.assertRaises(SystemExit) as stopped:
                    launcher.main()
            self.assertEqual(stopped.exception.code, 23)
            self.assertIs(sys.argv, invocation)


if __name__ == '__main__':
    unittest.main()
