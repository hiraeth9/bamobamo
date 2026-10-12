import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import duel_plan
import duel_worker
from scene_cast import validate_cast, write_cast


class CastTests(unittest.TestCase):
    def test_count_and_names_choose_internal_engine(self):
        for count, engine in ((1, 'single'), (2, 'duel'), (3, None)):
            actors = [{'id': chr(65+i), 'name': name} for i, name in enumerate(('张三', '李四', '王五')[:count])]
            cast = validate_cast({'actors': actors}, 'cast_test')
            self.assertEqual(cast['actor_count'], count)
            self.assertEqual(cast['engine'], engine)
            self.assertEqual(cast['supported'], count <= 2)
            self.assertEqual(cast['actors'], actors)

    def test_invalid_or_duplicate_people_rejected(self):
        for actors in ([], [{'id': 'A', 'name': ''}], [{'id': 'A', 'name': '甲'}, {'id': 'A', 'name': '乙'}], [{'id': 'B', 'name': '甲'}]):
            with self.assertRaises(ValueError):
                validate_cast({'actors': actors}, 'cast_test')

    def test_identity_mapping_is_immutable(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(duel_plan, 'JOBS', Path(folder)):
            write_cast('cast_test', {'actors': [{'id': 'A', 'name': '张三'}]})
            with self.assertRaises(ValueError):
                write_cast('cast_test', {'actors': [{'id': 'A', 'name': '李四'}]})

    def test_unsupported_count_or_wrong_engine_never_renders(self):
        for count in (1, 3):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                with patch.object(duel_plan, 'JOBS', folder), patch.object(duel_worker, 'JOBS', folder):
                    job = duel_plan.job_directory('cast_test')
                    duel_plan.atomic_json(job / 'request.json', {'prompt': '语义人物测试', 'mode': 'auto'})
                    def planning(*_):
                        write_cast('cast_test', {'actors': [{'id': chr(65+i), 'name': f'人物{i+1}'} for i in range(count)]})
                        duel_plan.atomic_json(job / 'plan.json', {'version': 1})
                    with patch.object(duel_worker, 'run_logged', side_effect=planning) as command:
                        result = duel_worker.run_job('cast_test')
                    self.assertFalse(result['ok'])
                    self.assertNotIn('video', result)
                    self.assertEqual(command.call_count, 1)
                    self.assertIn('人数不一致' if count == 1 else '3 名人物', result['error'])


if __name__ == '__main__':
    unittest.main()
