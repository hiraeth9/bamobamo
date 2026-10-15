from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.app import app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_bad_requests_do_not_launch_workers(self):
        with patch('web.app.submit_duel') as submit:
            for path in ('/api/jobs', '/api/generate', '/api/generate-single'):
                for payload in ({}, {'prompt': 10}, {'prompt': ' '}, ['not an object']):
                    self.assertEqual(self.client.post(path, json=payload).status_code, 400)
            submit.assert_not_called()

    def test_detailed_prompt_over_old_character_cap_reaches_hermes_entry(self):
        text = '详细描述。' * 1000
        for path in ('/api/jobs', '/api/generate', '/api/generate-single'):
            with self.subTest(path=path), patch('web.app.submit_duel', return_value='long_job') as submit, \
                    patch('web.app.duel_events', return_value=iter(())):
                response = self.client.post(path, json={'prompt': text})
            self.assertIn(response.status_code, (200, 202))
            submit.assert_called_once_with(text, None, mode='auto')

    def test_job_identity_and_idempotency_key_are_preserved(self):
        with patch('web.app.submit_duel', return_value='specific_job') as submit:
            response = self.client.post('/api/jobs', json={'prompt': '两个人出拳'}, headers={'Idempotency-Key': 'request-1'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['job_id'], 'specific_job')
        submit.assert_called_once_with('两个人出拳', 'request-1', mode='auto')

    def test_key_conflicts_and_missing_jobs_are_explicit(self):
        with patch('web.app.submit_duel', side_effect=ValueError('key conflict')):
            self.assertEqual(self.client.post('/api/jobs', json={'prompt': '两人对打'}).status_code, 409)
        self.assertEqual(self.client.get('/api/jobs/no_such_job').status_code, 404)

    def test_all_person_counts_use_one_semantic_entry(self):
        for prompt in ('a boxer practicing punches', '一个人连续两次出拳', 'A出拳，B格挡', '张三向李四反击', 'A和B夹击C，三个人全身入镜'):
            for route in ('/api/jobs', '/api/generate'):
                with self.subTest(prompt=prompt, route=route), patch('web.app.submit_duel', return_value='semantic_job') as submit, patch('web.app.duel_events', return_value=iter(())):
                    response = self.client.post(route, json={'prompt': prompt})
                    self.assertIn(response.status_code, (200, 202))
                    self.assertEqual(submit.call_args.kwargs['mode'], 'auto')

    def test_named_roles_use_semantic_agent_routing(self):
        with patch('web.app.submit_duel', return_value='named_pair') as submit:
            response = self.client.post('/api/jobs', json={'prompt': '张三出拳，李四格挡'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(submit.call_args.kwargs['mode'], 'auto')


if __name__ == '__main__':
    unittest.main()
