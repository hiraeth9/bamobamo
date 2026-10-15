"""Task boundaries and durable preview truth, without mocking Blender renders."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import agent_preview as preview
import duel_plan


class PreviewValidation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.jobs = Path(self.temporary.name)
        self.patch = patch.object(duel_plan, 'JOBS', self.jobs)
        self.patch.start()
        for name in ('preview_test', 'other_job'):
            job = self.jobs / name
            job.mkdir()
            (job / 'request.json').write_text(json.dumps({'prompt': name}))
        self.ident = 'a' * 32
        self.folder = preview._preview_folder('preview_test', self.ident, create=True)
        self.document = {'version': 1, 'job_id': 'preview_test', 'preview_id': self.ident,
                         'status': 'queued', 'requested_frames': [1], 'images': [],
                         'request_sha256': preview._sha(self.jobs / 'preview_test/request.json')}
        preview._update(self.folder, {}, self.document)

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def test_frames_and_resolution_reject_invalid_source_requests(self):
        scene = SimpleNamespace(frame_start=1, frame_end=9)
        self.assertEqual(preview._frames(scene, None), [1, 5, 9])
        self.assertEqual(preview._frames(scene, [1, 1, 9]), [1, 9])
        for frames in ([], [0], [10], [True], [1.5], [float('nan')], '../1'):
            with self.assertRaises(ValueError):
                preview._frames(scene, frames)
        for size in ([0, 1], [True, 200], [1.5, 2], [1, 2, 3]):
            with self.assertRaises(ValueError):
                preview._resolution(size)

    def test_filesystem_and_state_cannot_claim_other_task(self):
        for identity in ('../outside', None, 'a' * 31):
            with self.assertRaises(ValueError):
                preview._preview_folder('preview_test', identity)
        with self.assertRaises(ValueError):
            preview.preview_status('other_job', self.ident)
        other = self.jobs / 'other_job/agent_previews'
        other.mkdir()
        (other / self.ident).symlink_to(self.folder)
        with self.assertRaises(ValueError):
            preview.preview_status('other_job', self.ident)
        preview._update(self.folder, {'job_id': 'other_job'})
        with self.assertRaises(ValueError):
            preview.preview_status('preview_test', self.ident)

    def test_complete_claim_requires_saved_content_and_original_request(self):
        image = self.folder / 'frame.png'
        image.write_bytes(b'actual saved image bytes')
        record = {'frame': 1, 'filename': image.name, 'sha256': preview._sha(image)}
        preview._update(self.folder, {'status': 'complete', 'images': [record], 'process_exit_code': 0})
        self.assertTrue(preview.preview_status('preview_test', self.ident)['ok'])
        image.write_bytes(b'different bytes')
        self.assertFalse(preview.preview_status('preview_test', self.ident)['ok'])
        image.unlink()
        self.assertFalse(preview.preview_status('preview_test', self.ident)['ok'])
        image.write_bytes(b'actual saved image bytes')
        (self.jobs / 'preview_test/request.json').write_text(json.dumps({'prompt': 'changed'}))
        result = preview.preview_status('preview_test', self.ident)
        self.assertFalse(result['ok'])
        self.assertFalse(result['request_matches_current'])

    def test_interrupted_child_is_durably_failed_and_symlink_image_refused(self):
        document = dict(self.document, status='rendering', updated_at=time.time() - 10,
                        process={'pid': 99999999, 'start_ticks': '0', 'boot_id': 'unrelated'})
        (self.folder / 'status.json').write_text(json.dumps(document))
        self.assertFalse(preview.preview_status('preview_test', self.ident)['ok'])
        self.assertEqual(preview._read(self.folder)['status'], 'failed')
        image = self.folder / 'frame.png'
        image.symlink_to(self.jobs / 'preview_test/request.json')
        preview._update(self.folder, {'images': [{'filename': 'frame.png', 'sha256': 'x'}]})
        with self.assertRaises(ValueError):
            preview.preview_status('preview_test', self.ident)

    def test_pid_reuse_does_not_mark_old_preview_running(self):
        identity = preview._process_identity(os.getpid())
        self.assertTrue(preview._alive(identity))
        self.assertFalse(preview._alive(dict(identity, start_ticks='wrong')))
        self.assertIsNone(preview._process_identity(None))


if __name__ == '__main__':
    unittest.main()
